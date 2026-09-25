"""Referee extracted recalls against the textbooks and write a disputed-keys report.

Processes recall_items without a verdict (headline recalls first, then
variants), storing verdict, the textbook answer, verified quotes and the
explanation. Resumable: already-refereed items are skipped.

Usage (from backend/):
    python scripts/referee_recalls.py --source rafiullah-14 --limit 50
    python scripts/referee_recalls.py --source rafiullah-14 --headlines-only
    python scripts/referee_recalls.py --source rafiullah-14 --report disputed.csv   # report only
"""

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import case  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import RecallItem  # noqa: E402


def write_report(db, source: str, path: str) -> int:
    rows = (db.query(RecallItem)
            .filter(RecallItem.source == source, RecallItem.verdict.in_(("contradicted", "books_conflict")))
            .order_by(RecallItem.page, RecallItem.id).all())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["page", "chapter", "kind", "question", "published_answer", "verdict", "textbook_answer",
                    "evidence (book p.page: quote)", "explanation", "review_status"])
        for r in rows:
            ev = " | ".join(f"{e.get('book_title')} p.{e.get('page_number')}: {e.get('quote')}" for e in (r.evidence or []))
            w.writerow([r.page, r.chapter, r.kind, r.question, r.answer, r.verdict, r.textbook_answer, ev,
                        r.explanation, r.review_status])
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--headlines-only", action="store_true")
    parser.add_argument("--recheck-disputed", action="store_true",
                        help="re-judge items currently 'contradicted'/'books_conflict' (e.g. after a prompt change)")
    parser.add_argument("--report", help="write disputed-keys CSV to this path (and exit if nothing to referee)")
    args = parser.parse_args()

    from app.referee import judge
    from app.retrieval import get_reranker_model, get_second_stage_reranker, retrieval_service

    db = SessionLocal()
    if args.recheck_disputed:
        q = db.query(RecallItem).filter(RecallItem.source == args.source,
                                        RecallItem.verdict.in_(("contradicted", "books_conflict")))
    else:
        q = db.query(RecallItem).filter(RecallItem.source == args.source, RecallItem.verdict.is_(None))
    if args.headlines_only:
        q = q.filter(RecallItem.kind == "headline")
    q = q.order_by(case((RecallItem.kind == "headline", 0), else_=1), RecallItem.page, RecallItem.id)
    items = q.limit(args.limit).all() if args.limit else q.all()
    print(f"{len(items)} recalls to referee", flush=True)
    if items:
        retrieval_service._embed_query("warm up")
        get_reranker_model()
        get_second_stage_reranker()
    counts: dict[str, int] = {}
    for n, item in enumerate(items, 1):
        t0 = time.time()
        r = judge(db, item.question, answer=item.answer)
        if not r.get("verdict"):
            print(f"[{n}] #{item.id} ERROR {r.get('error')}", flush=True)
            continue
        previous = item.verdict
        item.verdict = r["verdict"]
        item.textbook_answer = r.get("textbook_answer")
        item.evidence = r.get("evidence")
        item.explanation = (r.get("explanation") or "") + (
            f"\n\nAI reasoning (not from the textbooks): {r['ai_reasoning']}" if r.get("ai_reasoning") else "")
        item.refereed_at = datetime.utcnow()
        db.commit()
        counts[item.verdict] = counts.get(item.verdict, 0) + 1
        change = f"{previous} -> " if args.recheck_disputed else ""
        print(f"[{n}/{len(items)}] p.{item.page} {change}{item.verdict:16} {time.time() - t0:4.1f}s  "
              f"{item.question[:70]} = {item.answer[:40]}", flush=True)
    print(f"Verdicts this run: {counts}", flush=True)
    if args.report:
        print(f"Disputed keys written: {write_report(db, args.source, args.report)} -> {args.report}")
    db.close()


if __name__ == "__main__":
    main()
