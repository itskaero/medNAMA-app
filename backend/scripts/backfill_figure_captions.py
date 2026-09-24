"""Attach printed captions to extracted figures and flag decorative images.

Ingestion stored every figure with a synthetic label ("Figure 295-47"), no
caption, and kept decorative images (icons, QR codes, bullets). The printed
captions were ingested as ordinary text chunks ("Figure 49.1. Angiofibroma.
Section shows multiple dilated vessels..."). This script:

  1. Decodes each image and flags it decorative when it is tiny
     (< 8 KB, shortest side < 120 px, or aspect ratio > 6).
  2. On each page, lists the non-decorative figures in document order and the
     caption chunks ("Figure 12.3 ..." / "Fig. 12-3 ...") in document order.
     They are paired only when the counts match exactly, or when there is one
     figure and one caption. Anything ambiguous stays uncaptioned (and is then
     never shown in answers).
  3. Stores the caption, the real label ("Figure 49.1"), caption_source =
     'printed', and a bge embedding of the caption for relevance matching.

Usage (from backend/):
    python scripts/backfill_figure_captions.py                 # dry run: counts + samples
    python scripts/backfill_figure_captions.py --apply         # write (back up the DB first)
    python scripts/backfill_figure_captions.py --book-id 2     # one book only

Re-running is safe: existing captions (from ingestion or an earlier run) are
kept unless --overwrite is given; an interrupted run simply continues.
"""

import argparse
import io
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402

# A caption starts the chunk, or starts a new sentence inside it ("... H&E. Figure 49.2 ...").
# In-text references ("see Figure 12.3") are not sentence starts and never split.
CAPTION_START = re.compile(r"(?:^\s*|(?<=[.;:)\]]\s))(?:Figure|Fig\.?)\s*(\d+[.\-]\d+[A-Za-z]?)\b", re.IGNORECASE)
MIN_BYTES = 8000
MIN_SIDE = 120
MAX_ASPECT = 6.0


def split_captions(content: str) -> list[tuple[str, str]]:
    """A caption chunk can hold several captions; return [(label_number, caption_text)]."""
    starts = list(CAPTION_START.finditer(content))
    if not starts or starts[0].start() > 3:
        return []
    out = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(content)
        body = " ".join(content[m.start():end].split())
        body = re.sub(r"\s*\[!\]\s*Scan to play.*$", "", body)
        out.append((m.group(1).replace("-", "."), body))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--overwrite", action="store_true",
                        help="clear and recompute captions that already exist (default: only fill missing ones)")
    parser.add_argument("--book-id", type=int, default=None)
    args = parser.parse_args()

    session = SessionLocal()
    book_filter = "AND f.book_id = :b" if args.book_id else ""
    params = {"b": args.book_id} if args.book_id else {}
    titles = dict(session.execute(text("SELECT id, title FROM books")).fetchall())

    # 1. Decorative detection
    print("Measuring images...")
    figs = session.execute(text(
        f"SELECT f.id, f.book_id, f.page_number, f.image_data FROM figures f WHERE true {book_filter} ORDER BY f.id"
    ), params).fetchall()
    meta: dict[int, tuple[int, int, bool]] = {}
    by_page: dict[tuple[int, int], list[int]] = defaultdict(list)
    for fid, book_id, page, data in figs:
        w = h = 0
        try:
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
        except Exception:
            pass
        decorative = (len(data) < MIN_BYTES or min(w, h) < MIN_SIDE
                      or (min(w, h) > 0 and max(w, h) / min(w, h) > MAX_ASPECT))
        meta[fid] = (w, h, decorative)
        if not decorative and page is not None:
            by_page[(book_id, page)].append(fid)
    del figs

    # 2. Caption chunks per page (parent chunks, document order)
    chunk_filter = "AND book_id = :b" if args.book_id else ""
    caps_by_page: dict[tuple[int, int], list[tuple[str, str]]] = defaultdict(list)
    rows = session.execute(text(
        f"SELECT id, book_id, page_number, content FROM chunks WHERE parent_id IS NULL AND page_number IS NOT NULL "
        f"AND content ~* '^\\s*(figure|fig\\.?)\\s*\\d+[.\\-]\\d+' {chunk_filter} ORDER BY id"
    ), params).fetchall()
    for _, book_id, page, content in rows:
        caps_by_page[(book_id, page)].extend(split_captions(content))

    # 3. Pair only when the page's figure and caption counts match exactly
    assignments: dict[int, tuple[str, str]] = {}
    stats: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for (book_id, page), fids in by_page.items():
        caps = caps_by_page.get((book_id, page), [])
        if caps and len(caps) == len(fids):
            assignments.update(zip(fids, caps))
            stats[book_id]["captioned"] += len(fids)
        else:
            stats[book_id]["unmatched"] += len(fids)

    print(f"\n{'book':30} {'captioned':>10} {'unmatched':>10}")
    for book_id, st in sorted(stats.items(), key=lambda x: -x[1]["captioned"]):
        print(f"{titles.get(book_id, book_id)!s:30} {st['captioned']:>10} {st['unmatched']:>10}")
    total_dec = sum(1 for m in meta.values() if m[2])
    print(f"\nFigures: {len(meta)} total, {total_dec} decorative, {len(assignments)} captioned, "
          f"{len(meta) - total_dec - len(assignments)} real but uncaptioned (never shown in answers).")

    sample = random.Random(7).sample(sorted(assignments), min(8, len(assignments)))
    print("\nSample pairs (figure id -> label | caption):")
    for fid in sample:
        num, cap = assignments[fid]
        print(f"  {fid:>6} -> Figure {num} | {cap[:110]}")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to write (back up the DB first).")
        return

    from app.ingestion import get_embedding_model

    model = get_embedding_model()
    print("\nWriting dimensions / decorative flags...")
    reset = ", caption = NULL, caption_source = NULL, caption_embedding = NULL" if args.overwrite else ""
    for fid, (w, h, dec) in meta.items():
        session.execute(text(
            f"UPDATE figures SET width = :w, height = :h, is_decorative = :d{reset} WHERE id = :i"
        ), {"w": w or None, "h": h or None, "d": dec, "i": fid})
    session.commit()

    if not args.overwrite:
        # Keep captions already present (from ingestion or an earlier run).
        have = {fid for (fid,) in session.execute(text(
            "SELECT id FROM figures WHERE caption IS NOT NULL AND caption_embedding IS NOT NULL"))}
        assignments = {fid: cap for fid, cap in assignments.items() if fid not in have}
    print(f"Embedding and writing {len(assignments)} captions...")
    items = sorted(assignments.items())
    for start in range(0, len(items), 128):
        batch = items[start:start + 128]
        vecs = model.encode([cap for _, (_, cap) in batch], normalize_embeddings=True, batch_size=32)
        for (fid, (num, cap)), vec in zip(batch, vecs):
            session.execute(text(
                "UPDATE figures SET caption = :c, figure_label = :l, caption_source = 'printed', "
                "caption_embedding = CAST(:e AS vector) WHERE id = :i"
            ), {"c": cap[:2000], "l": f"Figure {num}", "e": str(vec.tolist()), "i": fid})
        session.commit()
        print(f"  {min(start + 128, len(items))}/{len(items)}", flush=True)
    print("Done.")
    session.close()


if __name__ == "__main__":
    main()
