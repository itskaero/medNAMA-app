"""Retrieval metrics against a question's gold evidence, and the automatic failure-stage suggestion.

Gold evidence is optional per question:
  * "relevant_chunk_ids": verified parent chunk ids (preferred), or
  * "gold": a term predicate as in labs/evaluation/reranker ({"all": [...], "any": [[...]]}),
    matched by the reranker benchmark's own matcher (reused, not copied).

These numbers say whether the right evidence was found and ranked well. They are NOT the product
score: the answer is. A question with no gold evidence still gets answers, latency and human scores.
"""

from __future__ import annotations

import math
import sys
from typing import Any

from . import settings

_BENCH = settings.REPO / "labs" / "evaluation" / "reranker"
if str(_BENCH) not in sys.path:
    sys.path.insert(0, str(_BENCH))

from bench.common import Gold  # noqa: E402

KS = (1, 5, 10, 16)
FAILURE_CLASSES = ("retrieval", "reranking", "context_selection", "generation", "citation", "question_understanding")


def relevance_fn(item: dict[str, Any]):
    """A function chunk -> bool, or None when the question has no gold evidence."""
    ids = set(int(x) for x in item.get("relevant_chunk_ids") or [])
    if ids:
        return lambda chunk_id, text: chunk_id in ids
    gold = Gold.parse(item.get("gold"))
    if gold.empty():
        return None
    return lambda chunk_id, text: gold.matches(text)


def ranking_metrics(ranked_ids: list[int], rel: dict[int, bool]) -> dict[str, Any]:
    ranks = [r for r, cid in enumerate(ranked_ids, 1) if rel.get(cid)]
    n_rel = sum(1 for v in rel.values() if v)
    first = ranks[0] if ranks else None
    out: dict[str, Any] = {"rank_first_relevant": first, "relevant_in_pool": n_rel}
    for k in KS:
        out[f"recall@{k}"] = int(first is not None and first <= k)                     # hit-based
        out[f"recall_frac@{k}"] = (sum(1 for r in ranks if r <= k) / n_rel) if n_rel else 0.0
    out["mrr"] = 1.0 / first if first else 0.0
    dcg = sum(1.0 / math.log2(r + 1) for r in ranks if r <= 10)
    ideal = sum(1.0 / math.log2(r + 1) for r in range(1, min(10, n_rel) + 1))
    out["ndcg@10"] = dcg / ideal if ideal else 0.0
    return out


def suggest_failure(metrics: dict[str, Any] | None, context_relevant: bool | None, top_k: int) -> str | None:
    """Where a bad answer most likely went wrong, from the evidence trail alone. A suggestion for the
    human reviewer, who confirms it; generation/citation/understanding failures need a human."""
    if not metrics:
        return None
    if context_relevant:
        return None      # the evidence reached the LLM (directly or through paragraph expansion)
    if metrics["rank_first_relevant"] is None:
        return "retrieval"
    if metrics["rank_first_relevant"] > top_k:
        return "reranking"
    if context_relevant is False:
        return "context_selection"
    return None      # evidence reached the LLM; if the answer is still wrong, it is generation (human call)
