"""Tag which exam programme each MCQ can be used for (mcq_tags axis 'programme').

Programme was never in the data: it is implied by `main_category` strings and by
`past_papers.exam`, so nothing could ask "how many questions does PGET have?". This writes
it down, reusing the existing mcq_tags(mcq_id, axis, label) table rather than adding a column,
so practice_scope-style `IN (SELECT mcq_id ...)` filters work unchanged and no migration of
the mcqs table is needed.

A question may carry several programme labels, which is the point: the Paper 2 faculty pools
are shared between FCPS Part 1 and PGET. That is not an assumption - medmasters' own page says so
(MedMaster/main.html:18426: "PGET Surgery Groups reuse the SAME shared surg_p2_* content pool
as JCAT/FCPS"), and the family only changes the entitlement key, never the questions.

Mapping:
    FCPS-I    the whole of FCPS Part 1, which is sat as two papers of 100:
              Paper 1 = main_category 'Paper 1 - Basic sciences'
              Paper 2 = main_category 'Paper 2 - Faculty' (the candidate's specialty)
              plus every question in an 'FCPS Part 1' or 'FCPS Part 1 (Dentistry)' past paper
    PGET      the Paper 2 faculty pools (--no-pget to skip)

The Paper 2 faculty bank was first tagged FCPS-II here. That was wrong: it is FCPS Part 1's
specialty paper (medmasters' *_p2_* pools), not the FCPS Part 2 exam. Running this script removes
those stale labels from the questions it derives; it never touches a programme label on any
other question (an import that tags its own questions, such as a MediVerse programme, keeps
them).

FCPS-II, MBBS, MD, MS and USMLE are deliberately absent: no content derived here belongs to
them, and inventing a tag would make an empty programme look startable.

Usage (from backend/):
    python scripts/tag_programmes.py --dry-run
    python scripts/tag_programmes.py
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.retention import PAPER1, PAPER2  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tag-programmes")

AXIS = "programme"
PAST_EXAMS_FCPS1 = ("FCPS Part 1", "FCPS Part 1 (Dentistry)")


def ids_for(db, sql: str, params: dict) -> set[int]:
    return {r[0] for r in db.execute(text(sql), params).fetchall()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    ap.add_argument("--no-pget", action="store_true", help="do not tag the Paper 2 pools for PGET")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        ready = "status = 'ready' AND twist_of IS NULL"
        groups: dict[str, set[int]] = {}

        paper2 = ids_for(db, f"SELECT id FROM mcqs WHERE {ready} AND main_category = :c", {"c": PAPER2})
        groups["FCPS-I"] = ids_for(
            db, f"SELECT id FROM mcqs WHERE {ready} AND main_category = :c", {"c": PAPER1}
        ) | paper2 | ids_for(
            db,
            f"""SELECT m.id FROM mcqs m
                JOIN past_paper_questions q ON q.mcq_id = m.id
                JOIN past_papers p ON p.id = q.paper_id
                WHERE {ready.replace('status', 'm.status').replace('twist_of', 'm.twist_of')}
                  AND p.exam = ANY(:e)""",
            {"e": list(PAST_EXAMS_FCPS1)},
        )
        groups["FCPS-II"] = set()          # see the module docstring: the Paper 2 bank is not FCPS-II
        if not args.no_pget:
            # Same questions, different exam: see the module docstring.
            groups["PGET"] = set(paper2)

        existing = {
            (r[0], r[1]) for r in db.execute(
                text("SELECT mcq_id, label FROM mcq_tags WHERE axis = :a"), {"a": AXIS}).fetchall()
        }
        log.info("already tagged: %d rows", len(existing))
        # Stale labels: a derived question carrying a label this mapping no longer gives it.
        derived = set().union(*groups.values())
        for label, ids in groups.items():
            stale = [m for (m, lab) in existing if lab == label and m in derived and m not in ids]
            log.info("%-8s %6d stale labels to remove", label, len(stale))
            if args.dry_run or not stale:
                continue
            for start in range(0, len(stale), 5000):
                db.execute(
                    text("DELETE FROM mcq_tags WHERE axis = :a AND label = :l AND mcq_id = ANY(:ids)"),
                    {"ids": stale[start:start + 5000], "a": AXIS, "l": label},
                )
            db.commit()
        for label, ids in groups.items():
            fresh = [i for i in sorted(ids) if (i, label) not in existing]
            log.info("%-8s %6d questions  (%d new)", label, len(ids), len(fresh))
            if args.dry_run or not fresh:
                continue
            for start in range(0, len(fresh), 5000):
                db.execute(
                    text("INSERT INTO mcq_tags (mcq_id, axis, label) "
                         "SELECT unnest(CAST(:ids AS int[])), :a, :l ON CONFLICT DO NOTHING"),
                    {"ids": fresh[start:start + 5000], "a": AXIS, "l": label},
                )
            db.commit()

        rows = db.execute(text(
            "SELECT label, count(*) FROM mcq_tags WHERE axis = :a GROUP BY label ORDER BY label"),
            {"a": AXIS}).fetchall()
        log.info("--- programme tags now in the database ---")
        for label, n in rows:
            log.info("  %-8s %6d", label, n)
        if args.dry_run:
            log.info("(dry run: nothing was written)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
