"""Extract recalls (question -> published answer) and pearls from OCR'd pages.

Input:  data/private/<name>/pages/p0001.md ... (from scripts/ocr_book.py)
Output: recall_items rows (visibility 'admin') and concept_cards for pearls
        (source 'recall_book', grounding 'recall_book', visibility 'admin').

The source book is never ingested into the textbook library, so it can never be
cited as textbook evidence. Extraction favours accuracy: lines where OCR mixed
columns or the pairing is unclear are skipped, not guessed.

Resumable: processed pages are recorded in data/private/<name>/extracted.json.

Usage (from backend/):
    python scripts/extract_recalls.py --name rafiullah --source rafiullah-14
    python scripts/extract_recalls.py --name rafiullah --source rafiullah-14 --pages 20-60
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.llm import chat_completion  # noqa: E402
from app.models import ConceptCard, RecallItem  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

PROMPT = """You extract study material from ONE OCR'd page of an FCPS Part 1 recall/pearls book. The OCR is imperfect: columns are sometimes interleaved and words scrambled.

Return JSON:
{
  "chapter": "system/chapter name if the page shows it (e.g. 'Cardiovascular'), else null",
  "recalls": [
    {"question": "past-paper question stem, cleaned (fix obvious OCR typos only)",
     "answer": "the answer given by the book, cleaned",
     "kind": "headline | variant",
     "headline_no": 33 or null}
  ],
  "pearls": [
    {"topic": "e.g. Takayasu arteritis", "points": ["short fact", "..."], "mnemonic": "only if the page prints one, else null"}
  ],
  "skipped": number of recall-like lines you skipped
}

Rules - accuracy over quantity:
- A recall is a line where the book pairs a question with its answer, usually with "=", "-", ":" or "->" (e.g. "Superficial cardiac plexus is made by= Left vagus"). Numbered UPPERCASE headings like "33. ... = ..." are kind "headline" with their number.
- Only include a recall when the question and its answer clearly belong together in the text. If words from different columns are mixed, the question is incomplete, or you would have to guess the answer, SKIP it and count it in "skipped".
- Do not add facts from your own knowledge. Fix only obvious OCR spelling errors (e.g. "Wegner" -> "Wegener", "NA*" -> "Na+").
- Pearls: only coherent concept notes (headed topics with bullet facts). Skip scrambled fragments."""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    parser.add_argument("--source", required=True, help="label stored on each item, e.g. rafiullah-14")
    parser.add_argument("--pages", help="range like 20-60")
    args = parser.parse_args()

    base = REPO_ROOT / "data" / "private" / args.name
    pages_dir = base / "pages"
    state_file = base / "extracted.json"
    done = set(json.loads(state_file.read_text())) if state_file.exists() else set()
    files = sorted(pages_dir.glob("p*.md"))
    if args.pages:
        a, b = (int(x) for x in args.pages.split("-"))
        files = [f for f in files if a <= int(f.stem[1:]) <= b]
    todo = [f for f in files if f.stem not in done]
    print(f"{len(todo)} pages to extract ({len(done)} already done)", flush=True)

    db = SessionLocal()
    chapter = None
    totals = {"recalls": 0, "pearls": 0, "skipped": 0}
    try:
        for f in todo:
            text_ = f.read_text(encoding="utf-8").strip()
            page_no = int(f.stem[1:])
            if len(text_) < 200:
                done.add(f.stem)
                continue
            t0 = time.time()
            try:
                out = json.loads(chat_completion(
                    [{"role": "system", "content": PROMPT}, {"role": "user", "content": text_[:9000]}],
                    json_mode=True, temperature=0.0, max_tokens=4000, label="extract", role="fast",
                ))
            except Exception as e:
                print(f"{f.stem}: FAILED {type(e).__name__}: {e}", flush=True)
                continue
            chapter = (out.get("chapter") or chapter or None)
            n_recalls = n_pearls = 0
            for r in out.get("recalls") or []:
                q, a = str(r.get("question") or "").strip(), str(r.get("answer") or "").strip()
                if len(q) < 8 or not a or len(a) > 300:
                    continue
                exists = db.query(RecallItem.id).filter_by(source=args.source, page=page_no, question=q).first()
                if exists:
                    continue
                headline = r.get("headline_no")
                db.add(RecallItem(
                    source=args.source, page=page_no, chapter=chapter, question=q, answer=a,
                    kind="headline" if r.get("kind") == "headline" else "variant",
                    headline_no=headline if isinstance(headline, int) else None, visibility="admin",
                ))
                n_recalls += 1
            for p in out.get("pearls") or []:
                points = [str(x).strip() for x in (p.get("points") or []) if str(x).strip()]
                topic = str(p.get("topic") or "").strip()
                if not topic or not points:
                    continue
                db.add(ConceptCard(
                    title=topic[:200], summary=" ".join(f"• {x}" for x in points)[:1500],
                    mnemonic=(str(p.get("mnemonic")).strip()[:300] if p.get("mnemonic") else None),
                    subject=chapter, source="recall_book", grounding="recall_book", visibility="admin",
                    book_title=f"{args.source} (private)", page_number=page_no,
                ))
                n_pearls += 1
            db.commit()
            done.add(f.stem)
            state_file.write_text(json.dumps(sorted(done)))
            totals["recalls"] += n_recalls
            totals["pearls"] += n_pearls
            totals["skipped"] += int(out.get("skipped") or 0)
            print(f"{f.stem}: {n_recalls} recalls, {n_pearls} pearls, {out.get('skipped', 0)} skipped "
                  f"[{chapter}] {time.time() - t0:.1f}s", flush=True)
    finally:
        db.close()
    print(f"Done. {totals}", flush=True)


if __name__ == "__main__":
    main()
