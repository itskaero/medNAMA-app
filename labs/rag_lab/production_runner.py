"""Running production's chat answer path inside the lab, unchanged, and observing it.

`run_current()` calls backend/app/generation.generate_answer() exactly as the chat endpoint does for
a fresh question (no history; the endpoint only adds conversation storage around it). To record
what production did - its candidate pool, its reranking time, its final context - the lab wraps a
few methods *in its own process only*, for the duration of the call, and restores them afterwards.
No backend file is modified.

`with_context()` runs the same generate_answer() but hands it a lab configuration's context instead
of running production's search, so production's prompt can be compared on any context.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any

from . import settings  # noqa: F401

import app.retrieval as R  # noqa: E402
from app.generation import generate_answer  # noqa: E402


@contextmanager
def _observe():
    """Record production's search: pool handed to stage 1, stage timings, and the result."""
    seen: dict[str, Any] = {"stage1_ms": 0.0, "stage2_ms": 0.0, "pool": None}
    svc = R.retrieval_service
    orig_search = svc.search
    orig_rerank = R.RetrievalService.rerank_chunks
    orig_second = R.get_second_stage_reranker

    def rerank(self, query, parent_chunks, limit=5, texts=None):
        if seen["pool"] is None:
            seen["pool"] = [p.id for p in parent_chunks]
        t = time.perf_counter()
        try:
            return orig_rerank(self, query, parent_chunks, limit=limit, texts=texts)
        finally:
            seen["stage1_ms"] += (time.perf_counter() - t) * 1000

    class _Timed:
        def __init__(self, inner):
            self._inner = inner

        def predict(self, *a, **k):
            t = time.perf_counter()
            try:
                return self._inner.predict(*a, **k)
            finally:
                seen["stage2_ms"] += (time.perf_counter() - t) * 1000

    def second():
        m = orig_second()
        return _Timed(m) if m is not None else None

    def search(*a, **k):
        t = time.perf_counter()
        res = orig_search(*a, **k)
        seen["search_ms"] = (time.perf_counter() - t) * 1000
        seen["result"] = res
        seen["search_kwargs"] = {key: k[key] for key in ("limit", "level", "book_id", "book_ids") if key in k}
        return res

    R.RetrievalService.rerank_chunks = rerank
    R.get_second_stage_reranker = second
    svc.search = search
    try:
        yield seen
    finally:
        R.RetrievalService.rerank_chunks = orig_rerank
        R.get_second_stage_reranker = orig_second
        try:
            del svc.search          # drop the instance override; the class method shows through again
        except AttributeError:
            pass


def run_current(session, question: str, level: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(production's answer dict, what production did)."""
    with _observe() as seen:
        answer = generate_answer(session, question, level=level)
    return answer, seen


def with_context(session, question: str, level: str, context: list[Any], figures: list[dict],
                 sources: list[dict], queries: list[str]) -> dict[str, Any]:
    """Production's prompt and validation, on context the lab selected."""
    svc = R.retrieval_service

    def search(*a, **k):
        top = max((getattr(c, "score", 0.0) for c in context), default=None)
        return R.SearchResult(context=context, sources=sources, top_score=top, queries=queries, figures=figures)

    svc.search = search
    try:
        return generate_answer(session, question, level=level)
    finally:
        try:
            del svc.search
        except AttributeError:
            pass
