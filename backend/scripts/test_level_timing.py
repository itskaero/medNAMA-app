"""How long a level-aware search takes against a level-blind one.

The preferred-book probe adds one small filtered vector query per missed book, so the cost
needs to be a measured number rather than an assumption - the NAS reranks on CPU.

Usage (from backend/):
    python scripts/test_level_timing.py
"""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.retrieval import retrieval_service  # noqa: E402

logging.basicConfig(level=logging.WARNING)

QUERIES = ["treatment of diabetes mellitus", "mechanism of beta blockers", "DKA management"]


def main() -> None:
    db = SessionLocal()
    try:
        retrieval_service.search(db, "warm up the models", limit=3)   # exclude load time
        for query in QUERIES:
            row = []
            for level in (None, "FCPS-I", "FCPS-II"):
                t0 = time.monotonic()
                result = retrieval_service.search(db, query, limit=5, level=level)
                row.append((str(level), time.monotonic() - t0, len(result.context),
                            sorted({c.book.title.split()[0] for c in result.context if c.book})))
            print(f"\n{query!r}")
            for name, secs, n, books in row:
                print(f"   {name:<9} {secs:5.1f}s  {n} passages  {', '.join(books)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
