"""Rank past-paper recalls by how often they are asked, and pre-write high-yield questions.

1. Embeds every recall ("question = answer") that has no embedding yet.
2. For each headline recall, sets times_asked = the number of recalls in the
   bank (headlines and "also asked as" variants, any page) that are the same
   question reworded (cosine >= --threshold).
3. Optionally (--generate N) writes N textbook-grounded high-yield questions
   now, instead of letting the Daily Dose write them a few at a time.

Usage (from backend/):
    python scripts/rank_recalls.py --source rafiullah-14 --calibrate
    python scripts/rank_recalls.py --source rafiullah-14 [--threshold 0.86] [--generate 60]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import RecallItem  # noqa: E402


def embed_missing(db, source: str, batch: int = 64) -> int:
    from app.retention import _embed

    items = (db.query(RecallItem.id, RecallItem.question, RecallItem.answer)
             .filter(RecallItem.source == source, RecallItem.embedding.is_(None)).all())
    for i in range(0, len(items), batch):
        chunk = items[i:i + batch]
        vecs = _embed([f"{q} = {a}" for _, q, a in chunk])
        for (rid, _, _), v in zip(chunk, vecs):
            db.execute(text("UPDATE recall_items SET embedding = CAST(:v AS vector) WHERE id = :id"),
                       {"v": str(v.tolist()), "id": rid})
        db.commit()
        print(f"  embedded {min(i + batch, len(items))}/{len(items)}", flush=True)
    return len(items)


def calibrate(db, source: str) -> None:
    """Print sample neighbour pairs per similarity band, to choose the threshold by eye."""
    for lo, hi in ((0.95, 1.01), (0.90, 0.95), (0.86, 0.90), (0.82, 0.86), (0.78, 0.82)):
        rows = db.execute(text(
            "SELECT h.question, h.answer, r.question, r.answer, 1 - (h.embedding <=> r.embedding) AS sim "
            "FROM recall_items h JOIN recall_items r ON r.source = h.source AND r.id <> h.id "
            "WHERE h.source = :s AND h.kind = 'headline' "
            "AND 1 - (h.embedding <=> r.embedding) >= :lo AND 1 - (h.embedding <=> r.embedding) < :hi "
            "ORDER BY random() LIMIT 5"), {"s": source, "lo": lo, "hi": hi}).all()
        print(f"\n== cosine {lo:.2f}-{hi:.2f} ==")
        for hq, ha, rq, ra, sim in rows:
            print(f"  {sim:.3f}  {hq[:60]} = {ha[:25]}\n         {rq[:60]} = {ra[:25]}")


def rank(db, source: str, threshold: float) -> None:
    db.execute(text(
        "UPDATE recall_items h SET times_asked = sub.n FROM ("
        "  SELECT h2.id, 1 + count(r.id) AS n FROM recall_items h2 "
        "  LEFT JOIN recall_items r ON r.source = h2.source AND r.id <> h2.id "
        "    AND 1 - (h2.embedding <=> r.embedding) >= :t "
        "  WHERE h2.source = :s AND h2.kind = 'headline' AND h2.embedding IS NOT NULL GROUP BY h2.id"
        ") sub WHERE h.id = sub.id"), {"s": source, "t": threshold})
    db.commit()
    top = db.execute(text(
        "SELECT times_asked, chapter, question, answer FROM recall_items "
        "WHERE source = :s AND kind = 'headline' ORDER BY times_asked DESC NULLS LAST LIMIT 15"), {"s": source}).all()
    dist = db.execute(text(
        "SELECT times_asked, count(*) FROM recall_items WHERE source = :s AND kind = 'headline' "
        "GROUP BY times_asked ORDER BY times_asked"), {"s": source}).all()
    print("times_asked distribution:", {int(k or 0): int(v) for k, v in dist})
    print("most asked:")
    for n, ch, q, a in top:
        print(f"  {n:3}x [{ch}] {q[:70]} = {a[:30]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True)
    parser.add_argument("--threshold", type=float, default=0.86)
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--generate", type=int, default=0, help="write this many high-yield questions now")
    args = parser.parse_args()

    db = SessionLocal()
    print(f"Embedded {embed_missing(db, args.source)} recalls", flush=True)
    if args.calibrate:
        calibrate(db, args.source)
        return
    rank(db, args.source, args.threshold)

    if args.generate:
        from app.retention import generate_high_yield_mcq

        ids = db.execute(text(
            "SELECT id FROM recall_items WHERE source = :s AND kind = 'headline' AND verdict = 'supported' "
            "AND mcq_id IS NULL AND jsonb_typeof(evidence) = 'array' AND jsonb_array_length(evidence) > 0 "
            "ORDER BY times_asked DESC NULLS LAST, id LIMIT :n"), {"s": args.source, "n": args.generate}).scalars().all()
        made = 0
        for n, rid in enumerate(ids, 1):
            t0 = time.time()
            recall = db.get(RecallItem, rid)
            mcq = generate_high_yield_mcq(db, recall)
            made += mcq is not None
            print(f"[{n}/{len(ids)}] {'ok ' if mcq else 'FAIL'} {time.time() - t0:4.1f}s  "
                  f"{recall.times_asked}x {recall.question[:60]}", flush=True)
        print(f"High-yield questions written: {made}/{len(ids)}")
    db.close()


if __name__ == "__main__":
    main()
