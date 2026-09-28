"""Find reworded repeats across past-paper years and record every year each question was asked.

The archive export merges exact duplicates, so a question asked again in a later year usually
appears as a reworded copy. Two questions of the same exam from different years whose stem
embeddings have cosine >= --threshold (calibrated: 0.89; below it pairs are same-topic but
different questions) are treated as the same question asked again. mcqs.asked_years becomes the
union of their years; Rapid Review orders "most asked first" by it and the quiz shows the years.

Once scripts/link_recalls.py has run (two archives: Radiant and MediVerse), the years come from its
recall groups instead: those pairs are also checked by answer / option overlap, which plain cosine
is not, and with ~650 MediVerse sittings a generic stem would otherwise collect years it was never
asked in. --cosine forces the old rule. Run link_recalls.py first.

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
    parser.add_argument("--cosine", action="store_true",
                        help="ignore recall groups and link repeats by stem cosine alone (the pre-MediVerse rule)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    grouped = not args.cosine and bool(db.execute(text(
        "SELECT EXISTS (SELECT 1 FROM mcqs WHERE recall_group IS NOT NULL)")).scalar())
    # Grouped mode needs no vectors: not loading them keeps this light on the NAS.
    rows = db.execute(text(
        f"SELECT m.id, {'NULL' if grouped else 'm.stem_embedding::text'}, array_agg(DISTINCT p.year), m.recall_group "
        # This exam's questions, with the years of ALL their dated sittings: a question in both a Dentistry
        # and a general sitting must get the same years whichever exam is ranked last.
        "FROM mcqs m JOIN past_paper_questions q ON q.mcq_id = m.id JOIN past_papers p ON p.id = q.paper_id "
        "WHERE p.year IS NOT NULL AND m.id IN (SELECT q2.mcq_id FROM past_paper_questions q2 "
        "  JOIN past_papers p2 ON p2.id = q2.paper_id WHERE p2.exam = :e) GROUP BY m.id"), {"e": args.exam}).all()
    with_vec = [(i, e, set(y), g) for i, e, y, g in rows if e or grouped]
    print(f"{len(rows)} questions in {args.exam} past papers ({len(rows) - len(with_vec)} without embeddings)")
    ids = [r[0] for r in with_vec]
    years = [r[2] for r in with_vec]
    asked = [set(y) for y in years]
    pairs = 0
    if grouped:
        # link_recalls.py groups are answer/option-checked, so they span years and archives safely.
        # Members asked outside this exam's dated papers still count (e.g. a Radiant row's years).
        group_of = {r[0]: r[3] for r in with_vec if r[3] is not None}
        group_years: dict[int, set] = {}
        for gid, y in db.execute(text(
                "SELECT m.recall_group, p.year FROM mcqs m JOIN past_paper_questions q ON q.mcq_id = m.id "
                "JOIN past_papers p ON p.id = q.paper_id WHERE m.recall_group = ANY(:g) AND p.year IS NOT NULL"),
                {"g": list(set(group_of.values()))}):
            group_years.setdefault(gid, set()).add(y)
        for k, i in enumerate(ids):
            extra = group_years.get(group_of.get(i), set()) - asked[k]
            pairs += bool(extra)
            asked[k] |= extra
        print(f"using recall groups (link_recalls.py): {pairs} questions gained years from their group")
    else:
        E = np.asarray([json.loads(r[1]) for r in with_vec], dtype=np.float32)
        E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
        for start in range(0, len(ids), 1000):
            S = E[start:start + 1000] @ E.T
            for a in range(S.shape[0]):
                i = start + a
                for j in np.nonzero(S[a] >= args.threshold)[0]:
                    if j != i and not (years[i] & years[j]):   # a reworded repeat in another year
                        asked[i] |= years[j]
                        pairs += 1
        print(f"cross-year repeat links (cosine): {pairs // 2}")
    counts = {}
    for y in asked:
        counts[len(y)] = counts.get(len(y), 0) + 1
    print(f"questions by number of years asked: {dict(sorted(counts.items()))}")
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
