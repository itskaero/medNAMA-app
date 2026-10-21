"""Grounded-answer evaluation harness (revision.md §6, Phase 1 "evaluation-first").

Runs the *production* ``generate_answer`` unchanged over a question set and measures how
grounded the answers actually are, so any grounding change is judged by evidence rather
than assumption (§6 "guided by evaluation rather than assumptions").

Two kinds of metric, kept deliberately separate:

* **Mechanical (no labels needed).** grounding class, abstention, citation count, how much
  of the answer is textbook vs. AI supplement, and whether each citation points at a
  retrieved passage. These are computed from the pipeline's own output.
* **Labelled (needs review).** a question may carry ``expected_grounding``,
  ``should_abstain`` and per-citation ``supported`` flags. Filling those in is a clinical
  judgement, so they default to absent and the labelled metrics are reported only when
  present. The harness never invents them.

The question set reuses ``labs/rag_lab/questions.json`` (12 starter questions). Those are
flagged there as *not clinician-reviewed*; treat the labelled metrics as a scaffold a
reviewer completes, not as ground truth.

Usage (from the repository root, dev DB up, LLM key in the root .env):

    .venv-gpu/Scripts/python.exe -m labs.evaluation.grounding.eval_grounding \
        --questions labs/rag_lab/questions.json --out labs/evaluation/grounding/report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.generation import NOT_COVERED_RESPONSE, is_refusal_text  # noqa: E402

DEFAULT_QUESTIONS = REPO / "labs" / "rag_lab" / "questions.json"


def _supplement_ratio(textbook_md: str, supplement_md: str) -> float:
    """Share of the answered text that is uncited AI supplement (0..1)."""
    t = len((textbook_md or "").strip())
    s = len((supplement_md or "").strip())
    total = t + s
    return round(s / total, 3) if total else 0.0


def _citation_in_context(citation: dict, sources: list[dict]) -> bool:
    """Does the cited (book, page) appear among the retrieved sources?

    ``validate_generation`` already strips citations that were not in the retrieved context,
    so this should hold for every surviving citation; it is checked rather than assumed.
    """
    title = str(citation.get("book_title", "")).strip().lower()
    page = citation.get("page_number")
    for src in sources:
        if (str(src.get("book_title", "")).strip().lower() == title
                and src.get("page_number") == page):
            return True
    return False


def evaluate_question(db, question: dict) -> dict:
    from app.generation import generate_answer

    q = question["question"]
    level = question.get("level")
    intent = question.get("intent")
    result = generate_answer(db, q, level=level)
    citations = result.get("citations", [])
    sources = result.get("sources", [])
    textbook_md = result.get("textbook_answer_markdown") or ""
    supplement_md = result.get("supplementary_markdown") or ""
    answered = result.get("answer_markdown") or ""

    mechanical = {
        "id": question.get("id"),
        "question": q,
        "level": level,
        "intent": intent,
        "grounding": result.get("grounding"),
        "status": result.get("status"),
        "abstained": is_refusal_text(answered) or answered.startswith(NOT_COVERED_RESPONSE["answer_markdown"][:40]),
        "n_citations": len(citations),
        "n_sources": len(sources),
        "top_relevance": (sources[0].get("relevance_score") if sources else None),
        "supplement_ratio": _supplement_ratio(textbook_md, supplement_md),
        "citations_in_context": sum(_citation_in_context(c, sources) for c in citations),
    }

    # Labelled metrics: only computed when the question carries review flags.
    label = question.get("review") or {}
    labelled = {}
    if "expected_grounding" in label:
        labelled["grounding_matches_expected"] = (
            result.get("grounding") == label["expected_grounding"])
    if "should_abstain" in label:
        labelled["abstention_correct"] = (
            mechanical["abstained"] == bool(label["should_abstain"]))
    if label.get("citation_supported") is not None:
        labelled["citations_supported"] = int(sum(label["citation_supported"]))
        labelled["citations_total"] = len(label["citation_supported"])

    return {"mechanical": mechanical, "labelled": labelled,
            "answer_markdown": answered, "citations": citations, "sources": sources}


def aggregate(results: list[dict]) -> dict:
    mech = [r["mechanical"] for r in results]
    n = len(mech) or 1
    grounding_counts: dict[str, int] = {}
    for m in mech:
        grounding_counts[m["grounding"]] = grounding_counts.get(m["grounding"], 0) + 1
    summary = {
        "n_questions": len(mech),
        "grounding_distribution": grounding_counts,
        "abstention_rate": round(sum(m["abstained"] for m in mech) / n, 3),
        "mean_citations": round(sum(m["n_citations"] for m in mech) / n, 2),
        "mean_sources": round(sum(m["n_sources"] for m in mech) / n, 2),
        "mean_supplement_ratio": round(sum(m["supplement_ratio"] for m in mech) / n, 3),
        "citations_in_context_rate": (
            round(sum(m["citations_in_context"] for m in mech) / max(1, sum(m["n_citations"] for m in mech)), 3)
        ),
    }
    # Labelled aggregates, only over questions that carry the label.
    exp = [r["labelled"] for r in results if "grounding_matches_expected" in r["labelled"]]
    if exp:
        summary["grounding_accuracy"] = round(
            sum(1 for e in exp if e["grounding_matches_expected"]) / len(exp), 3)
        summary["n_grounding_labelled"] = len(exp)
    abst = [r["labelled"] for r in results if "abstention_correct" in r["labelled"]]
    if abst:
        summary["abstention_accuracy"] = round(
            sum(1 for e in abst if e["abstention_correct"]) / len(abst), 3)
        summary["n_abstention_labelled"] = len(abst)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    ap.add_argument("--out", default=str(Path(__file__).with_name("report.json")))
    ap.add_argument("--limit", type=int, default=0, help="only the first N questions (smoke test)")
    args = ap.parse_args()

    questions = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    if args.limit:
        questions = questions[: args.limit]

    db = SessionLocal()
    results = []
    try:
        for i, q in enumerate(questions, 1):
            started = time.time()
            try:
                res = evaluate_question(db, q)
            except Exception as e:  # a single question must not sink the run
                res = {"mechanical": {"id": q.get("id"), "question": q["question"], "grounding": None,
                                      "status": "error", "error": f"{type(e).__name__}: {e}"},
                       "labelled": {}}
            res["mechanical"]["elapsed_s"] = round(time.time() - started, 1)
            results.append(res)
            m = res["mechanical"]
            print(f"[{i}/{len(questions)}] {m.get('id')}: grounding={m.get('grounding')} "
                  f"citations={m.get('n_citations')} abstained={m.get('abstained')} "
                  f"({m.get('elapsed_s')}s)", flush=True)
    finally:
        db.close()

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "questions_file": str(args.questions),
        "summary": aggregate(results),
        "results": results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print("\n=== grounding baseline ===")
    for k, v in report["summary"].items():
        print(f"  {k}: {v}")
    print(f"\nreport -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
