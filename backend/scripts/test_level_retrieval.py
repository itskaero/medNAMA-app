"""Does exam level steer retrieval without overpowering relevance, and is source scope hard?

Determinism matters here. The query rewrite is an LLM call, and even at temperature 0 it is
not bit-reproducible, so two runs of the same query can retrieve different passages (the same
Nelson page was seen reranking at +3.67 and +3.80 on consecutive runs). A single run therefore
cannot tell a real effect from run-to-run noise.

So this reports two things:
  * rewrite OFF - deterministic, isolates what the level logic itself does
  * rewrite ON  - repeated trials, reported as a frequency rather than a yes/no

Usage (from backend/):
    python scripts/test_level_retrieval.py
    python scripts/test_level_retrieval.py --trials 5
"""

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.exam_levels import classify_intent, preferred_subjects  # noqa: E402
from app.models import Book  # noqa: E402
from app.retrieval import retrieval_service  # noqa: E402

logging.basicConfig(level=logging.WARNING)

QUERIES = ["diabetes mellitus", "treatment of diabetes mellitus"]
LEVELS = [None, "FCPS-I", "FCPS-II", "USMLE"]


def context_of(db, query, level, *, rewrite=True, book_ids=None):
    result = retrieval_service.search(db, query, limit=5, level=level, rewrite=rewrite,
                                      book_ids=book_ids)
    return [((c.book.title if c.book else "?"), (c.book.subject if c.book else None) or "-",
             c.page_number, c.score) for c in result.context]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=3, help="repeats for the rewrite-ON pass")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        books = {b.title: b.id for b in db.query(Book).filter(Book.status == "ready").all()}

        print("=" * 78)
        print("PASS 1 - rewrite OFF (deterministic): what the level logic itself does")
        for query in QUERIES:
            print(f"\nQUERY {query!r}  intent={classify_intent(query)}")
            for level in LEVELS:
                wanted = preferred_subjects(level)
                rows = context_of(db, query, level, rewrite=False)
                print(f"  level={str(level):<9}")
                for title, subject, page, score in rows:
                    print(f"    {'*' if subject in wanted else ' '} {title[:34]:<34} "
                          f"{subject[:12]:<12} p{str(page or '?'):<5} {score:+.2f}")

        print("\n" + "=" * 78)
        print(f"PASS 2 - rewrite ON, {args.trials} trials: how often each book reaches the context")
        for query in QUERIES:
            print(f"\nQUERY {query!r}")
            for level in LEVELS:
                hits: Counter = Counter()
                for _ in range(args.trials):
                    for title, _subject, _page, _score in context_of(db, query, level):
                        hits[title.split()[0]] += 1
                seen = ", ".join(f"{b}x{n}" for b, n in hits.most_common())
                print(f"  level={str(level):<9} {seen}")

        print("\n" + "=" * 78)
        scope = [i for t, i in books.items() if "Katzung" in t or "Guyton" in t]
        print(f"PASS 3 - hard scope, book_ids={scope} (Katzung + Guyton only)")
        ok = True
        for level in LEVELS:
            titles = {t for t, _s, _p, _sc in
                      context_of(db, "treatment of diabetes mellitus", level, book_ids=scope)}
            leaked = [t for t in titles if "Katzung" not in t and "Guyton" not in t]
            if leaked:
                ok = False
            print(f"  level={str(level):<9} {sorted(titles)}{'  !! LEAK' if leaked else ''}")
        print(f"\n  scope respected at every level: {'YES' if ok else 'NO'}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
