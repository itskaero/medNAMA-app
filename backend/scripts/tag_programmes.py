"""Tag which exam programme each MCQ can be used for (mcq_tags axis 'programme').

Programme was never in the data: it is implied by `main_category` strings and by
`past_papers.exam`, so nothing could ask "how many questions does PGET have?". This writes
it down, reusing the existing mcq_tags(mcq_id, axis, label) table rather than adding a column,
so practice_scope-style `IN (SELECT mcq_id ...)` filters work unchanged and no migration of
the mcqs table is needed.

A question may carry several programme labels, which is the point: the Paper 2 faculty pools
are shared between FCPS-II and PGET. That is not an assumption - medmasters' own page says so
(MedMaster/main.html:18426: "PGET Surgery Groups reuse the SAME shared surg_p2_* content pool
as JCAT/FCPS"), and the family only changes the entitlement key, never the questions.

Mapping:
    FCPS-I    main_category = 'Paper 1 - Basic sciences', or a question in an
              'FCPS Part 1' past paper
    FCPS-II   main_category = 'Paper 2 - Faculty'
    PGET      the same Paper 2 pools (--no-pget to skip)

MBBS, MD, MS and USMLE are deliberately absent: no content in this database belongs to them,
and inventing a tag would make an empty programme look startable.

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
PAST_EXAM_FCPS1 = "FCPS Part 1"


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

        groups["FCPS-I"] = ids_for(
            db, f"SELECT id FROM mcqs WHERE {ready} AND main_category = :c", {"c": PAPER1}
        ) | ids_for(
            db,
            f"""SELECT m.id FROM mcqs m
                JOIN past_paper_questions q ON q.mcq_id = m.id
                JOIN past_papers p ON p.id = q.paper_id
                WHERE {ready.replace('status', 'm.status').replace('twist_of', 'm.twist_of')}
                  AND p.exam = :e""",
            {"e": PAST_EXAM_FCPS1},
        )
        paper2 = ids_for(db, f"SELECT id FROM mcqs WHERE {ready} AND main_category = :c", {"c": PAPER2})
        groups["FCPS-II"] = paper2
        if not args.no_pget:
            # Same questions, different exam: see the module docstring.
            groups["PGET"] = set(paper2)

        existing = {
            (r[0], r[1]) for r in db.execute(
                text("SELECT mcq_id, label FROM mcq_tags WHERE axis = :a"), {"a": AXIS}).fetchall()
        }
        log.info("already tagged: %d rows", len(existing))
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
