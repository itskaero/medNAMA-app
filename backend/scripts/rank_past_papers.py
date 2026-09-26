"""Find reworded repeats across past-paper years and record every year each question was asked.

The archive export merges exact duplicates, so a question asked again in a later year usually
appears as a reworded copy. Two questions of the same exam from different years whose stem
embeddings have cosine >= --threshold (calibrated: 0.89; below it pairs are same-topic but
different questions) are treated as the same question asked again. mcqs.asked_years becomes the
union of their years; Rapid Review orders "most asked first" by it and the quiz shows the years.

Needs only numpy and the database (no ML model is loaded), so it is safe on the NAS:
    docker exec -w /app/backend mednama-backend python scripts/rank_past_papers.py --exam "FCPS Part 1"
Usage (from backend/):
    python scripts/rank_past_papers.py --exam "FCPS Part 1" [--threshold 0.89] [--dry-run]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exam", default="FCPS Part 1")
    parser.add_argument("--threshold", type=float, default=0.89)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    rows = db.execute(text(
        "SELECT m.id, m.stem_embedding::text, array_agg(DISTINCT p.year) FROM mcqs m "
        "JOIN past_paper_questions q ON q.mcq_id = m.id JOIN past_papers p ON p.id = q.paper_id "
        "WHERE p.exam = :e AND p.year IS NOT NULL GROUP BY m.id"), {"e": args.exam}).all()
    with_vec = [(i, e, set(y)) for i, e, y in rows if e]
    print(f"{len(rows)} questions in {args.exam} past papers ({len(rows) - len(with_vec)} without embeddings)")
    ids = [r[0] for r in with_vec]
    years = [r[2] for r in with_vec]
    E = np.asarray([json.loads(r[1]) for r in with_vec], dtype=np.float32)
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
    asked = [set(y) for y in years]
    pairs = 0
    for start in range(0, len(ids), 1000):
        S = E[start:start + 1000] @ E.T
        for a in range(S.shape[0]):
            i = start + a
            for j in np.nonzero(S[a] >= args.threshold)[0]:
                if j != i and not (years[i] & years[j]):   # a reworded repeat in another year
                    asked[i] |= years[j]
                    pairs += 1
    counts = {}
    for y in asked:
        counts[len(y)] = counts.get(len(y), 0) + 1
    print(f"cross-year repeat links: {pairs // 2}; questions by number of years asked: {dict(sorted(counts.items()))}")
    for i, y in [(i, y) for i, y in zip(ids, asked) if len(y) >= 3][:8]:
        stem = db.execute(text("SELECT question_text FROM mcqs WHERE id = :i"), {"i": i}).scalar()
        print(f"   {sorted(y)}  {' '.join(stem.split())[:90]}")
    if args.dry_run:
        return
    for k in range(0, len(ids), 2000):
        db.execute(text("UPDATE mcqs SET asked_years = CAST(v.y AS jsonb) FROM (SELECT unnest(CAST(:ids AS int[])) AS id, "
                        "unnest(CAST(:ys AS text[])) AS y) v WHERE mcqs.id = v.id"),
                   {"ids": ids[k:k + 2000], "ys": [json.dumps(sorted(y)) for y in asked[k:k + 2000]]})
    db.commit()
    print("asked_years written.")


if __name__ == "__main__":
    main()
