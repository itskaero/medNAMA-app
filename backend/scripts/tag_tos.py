"""Tag questions with their TOS topic and subtopic (mcq_tags axes tos_topic / tos_subtopic / tos_review).

See app/tos_tags.py for how a topic is chosen and how accurate it is. Re-run after a TOS file changes or a
large import: each paper's tags are replaced, so a re-run is safe.

Usage (from backend/):
    python scripts/tag_tos.py --dry-run                 # counts only, write nothing
    python scripts/tag_tos.py                           # every paper of every exam
    python scripts/tag_tos.py --exam pget --paper mbbs-p1
    python scripts/tag_tos.py --review                  # list questions held for review (close calls)
    python scripts/tag_tos.py --save-vectors v.npz      # also write the topic-text vectors used
    python scripts/tag_tos.py --vectors v.npz           # use them: no embedding model needed (small servers)
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import MCQ, MCQTag  # noqa: E402
from app.tos import tos_registry  # noqa: E402
from app import tos_tags  # noqa: E402
from app.tos_tags import AXIS_REVIEW, AXIS_TOPIC, tag_paper  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tag-tos")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exam", help="pget | fcps1 | mbbs (default: all)")
    ap.add_argument("--paper", help="one paper id, e.g. mbbs-p1")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--review", action="store_true", help="print questions held for review and exit")
    ap.add_argument("--vectors", help="topic-text vectors from --save-vectors (skips loading the model)")
    ap.add_argument("--save-vectors", help="write the topic-text vectors used to this .npz")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.review:
            rows = (db.query(MCQTag.label, MCQ.id, MCQ.question_text).join(MCQ, MCQ.id == MCQTag.mcq_id)
                    .filter(MCQTag.axis == AXIS_REVIEW).order_by(MCQTag.label).limit(500).all())
            for label, mid, stem in rows:
                print(f"{label}\t{mid}\t{(stem or '')[:100]}")
            total = db.query(func.count()).select_from(MCQTag).filter(MCQTag.axis == AXIS_REVIEW).scalar()
            print(f"\n{total} question/paper pairs held for review (first 500 shown)")
            return 0

        if args.vectors:
            log.info("Loaded %d topic vectors from %s", tos_tags.load_vectors(args.vectors), args.vectors)
        t0 = time.time()
        totals = {"pool": 0, "seeded": 0, "confident": 0, "review": 0, "off": 0, "empty_topics": 0, "topics": 0}
        for exam_id, exam in tos_registry.examinations.items():
            if args.exam and exam_id != args.exam:
                continue
            for paper in exam["papers"]:
                if args.paper and paper["id"] != args.paper:
                    continue
                st = tag_paper(db, exam_id, paper, dry_run=args.dry_run)
                n_topics = sum(len(s["topics"]) for s in paper["subjects"])
                empty = n_topics - len(st.per_topic)
                log.info("%-6s %-40s pool %6d  seeded %4d  confident %6d  review %5d  off %5d  topics w/o Qs %d/%d",
                         exam_id, paper["id"][:40], st.pool, st.seeded, st.confident, st.review, st.off_syllabus,
                         empty, n_topics)
                for k, v in (("pool", st.pool), ("seeded", st.seeded), ("confident", st.confident),
                             ("review", st.review), ("off", st.off_syllabus), ("empty_topics", empty),
                             ("topics", n_topics)):
                    totals[k] += v
        if args.save_vectors:
            log.info("Saved %d topic vectors to %s", tos_tags.save_vectors(args.save_vectors), args.save_vectors)
        if args.vectors:
            log.info("Topic texts not in the vector file (embedded here): %d", tos_tags._VECTORS_MISSED)
        log.info("TOTAL %s in %.0fs%s", totals, time.time() - t0, " (dry run: nothing written)" if args.dry_run else "")
        if not args.dry_run:
            n = db.query(func.count()).select_from(MCQTag).filter(MCQTag.axis == AXIS_TOPIC).scalar()
            log.info("tos_topic tags now: %d", n)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
