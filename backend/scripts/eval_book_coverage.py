"""Does the library find the answer? A before/after check for re-ingesting books.

Takes a fixed random sample of past-paper questions (the same sample every run, seeded), searches the
library with the question and its correct option the way the Referee does, and counts a hit when a
top-k passage contains the correct option's words. No AI calls, so it is cheap and repeatable:

    python scripts/eval_book_coverage.py --n 300 --out ../data/coverage-before.json
    ... re-ingest ...
    python scripts/eval_book_coverage.py --n 300 --out ../data/coverage-after.json --compare ../data/coverage-before.json

Run it inside the backend container (it needs the embedding model and the reranker).
"""

import argparse
import json
import random
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.retrieval import retrieval_service  # noqa: E402

_WORD = re.compile(r"[a-z0-9]+")
STOP = {"the", "of", "and", "a", "an", "in", "to", "is", "by", "with", "for", "on", "or", "at", "as", "from"}


def words(s: str) -> list[str]:
    return [w for w in _WORD.findall((s or "").lower()) if w not in STOP]


def hit(answer: str, passage: str) -> bool:
    want = words(answer)
    if not want:
        return False
    have = set(words(passage))
    return sum(1 for w in want if w in have) / len(want) >= (1.0 if len(want) <= 2 else 0.75)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", required=True)
    ap.add_argument("--compare")
    args = ap.parse_args()

    db = SessionLocal()
    rows = db.execute(text(
        "SELECT id, question_text, options, correct_option, sub_category FROM mcqs "
        "WHERE twist_of IS NULL AND main_category LIKE 'Paper%' AND status = 'ready' ORDER BY id")).fetchall()
    rng = random.Random(args.seed)
    sample = rng.sample(rows, min(args.n, len(rows)))
    results = {}
    by_subject = defaultdict(lambda: [0, 0])
    t0 = time.monotonic()
    for i, (mid, stem, options, key, subject) in enumerate(sample, 1):
        opts = options if isinstance(options, dict) else json.loads(options or "{}")
        answer = str(opts.get(key) or "")
        if not answer:
            continue
        chunks = retrieval_service.hybrid_search(db, f"{stem}\n{answer}", limit=args.k)
        ok = any(hit(answer, c.content) for c in chunks)
        books = sorted({c.book.title for c in chunks if c.book})
        results[str(mid)] = {"hit": ok, "subject": subject, "books": books}
        by_subject[subject][0] += ok
        by_subject[subject][1] += 1
        if i % 50 == 0:
            print(f"  {i}/{len(sample)} ({time.monotonic() - t0:.0f}s)")
    total = sum(v["hit"] for v in results.values())
    print(f"\nAnswer found in the top {args.k} passages: {total}/{len(results)} ({total / max(1, len(results)):.0%})")
    for s, (h, n) in sorted(by_subject.items(), key=lambda kv: -kv[1][1]):
        print(f"  {s or '?':32} {h:>4}/{n:<4} {h / max(1, n):.0%}")
    Path(args.out).write_text(json.dumps({"k": args.k, "results": results}, indent=0), encoding="utf-8")

    if args.compare:
        before = json.loads(Path(args.compare).read_text(encoding="utf-8"))["results"]
        common = [m for m in results if m in before]
        gained = sum(1 for m in common if results[m]["hit"] and not before[m]["hit"])
        lost = sum(1 for m in common if before[m]["hit"] and not results[m]["hit"])
        b = sum(before[m]["hit"] for m in common)
        a = sum(results[m]["hit"] for m in common)
        print(f"\nCompared with {args.compare} on {len(common)} questions: {b} -> {a} "
              f"(+{gained} newly found, -{lost} no longer found)")
    db.close()


if __name__ == "__main__":
    main()
