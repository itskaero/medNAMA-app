"""Retrieval-only coverage probe for the grounding harness.

Runs the production ``retrieval_service.search`` over a question set and reports, per question,
the top reranker score and how many context blocks were found. This is the objective signal for
"is this question covered by the library?" - it needs no LLM and no clinical label.

Score bands follow the calibration already in ``app/retrieval.py`` (relevant >= -2, off-topic
<= -8 for the ms-marco stage-1 model). A question whose top score is weak or whose context is
empty is a candidate for abstention; a well-scored one is safe to answer.

Usage:
    .venv-gpu/Scripts/python.exe -m labs.evaluation.grounding.probe_coverage \
        --questions labs/evaluation/grounding/uncovered_questions.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.retrieval import retrieval_service  # noqa: E402

# Stage-1 reranker bands (see app/retrieval.py MIN_RERANK_SCORE and its comment).
COVERED_MIN = -2.0
WEAK_MIN = -8.0


def band(score: float | None) -> str:
    if score is None:
        return "no_context"
    if score >= COVERED_MIN:
        return "covered"
    if score >= WEAK_MIN:
        return "weak"
    return "off_topic"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    args = ap.parse_args()

    questions = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    db = SessionLocal()
    try:
        for q in questions:
            result = retrieval_service.search(db, q["question"], level=q.get("level"))
            top = result.top_score
            src = result.sources[0] if result.sources else {}
            print(f"{q['id']:8} [{q.get('category','?'):18}] top={top if top is None else round(top,2)!s:>7} "
                  f"band={band(top):10} n_ctx={len(result.context)} "
                  f"top_src={src.get('book_title','-')} p{src.get('page_number','-')}", flush=True)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
