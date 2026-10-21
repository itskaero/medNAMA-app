"""Candidate retrieval for the lab, built from production's own primitives (read-only reuse).

Reused unchanged from backend/app/retrieval.py: rewrite_query, RetrievalService._embed_query,
vector_search, keyword_search, looks_like_index_page, _focus_text, CANDIDATE_LIMIT, PER_LIST_TOP.

Reproduced here (so it can be varied without touching production): the RRF fusion loop of
search(). Production fuses with k = 60 and equal weights, then reranks the top 16 by RRF plus the
top 3 of every list. The lab makes k, the vector/keyword weights and the pool size configurable.
With k = 60, equal weights and pool 16 the pool is production's (labs/evaluation/reranker verified 19/20
identical; the 20th differed only by production's own run-to-run variation).

Not reproduced: production's exam-level extras (per-book probes and per-book pool heads when a
level is set). The lab applies the exam level only as production's soft ordering bonus, after
reranking. The CURRENT configuration runs production itself and so includes them.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import settings  # noqa: F401

from sqlalchemy.orm import joinedload  # noqa: E402

from app.models import Chunk  # noqa: E402
from app.retrieval import (  # noqa: E402
    CANDIDATE_LIMIT, PER_LIST_TOP, _focus_text, looks_like_index_page, retrieval_service, rewrite_query,
)


@dataclass
class Candidate:
    chunk: Any                              # the parent Chunk (ORM), as production's later stages expect
    focus: str                              # the 1,500-char window rerankers score
    best_child: str                         # the matched child text (production's expand_context focus)
    rrf: float
    rrf_rank: int
    signals: dict[str, Any] = field(default_factory=dict)
    in_production_pool: bool = False
    flags: list[str] = field(default_factory=list)
    source: str = "retrieval"               # retrieval | targeted:<component>

    @property
    def id(self) -> int:
        return self.chunk.id

    def summary(self) -> dict[str, Any]:
        c = self.chunk
        return {"id": c.id, "book": c.book.title if c.book else None, "book_subject": c.book.subject if c.book else None,
                "chapter": c.chapter, "page": c.page_number, "rrf": round(self.rrf, 6), "rrf_rank": self.rrf_rank,
                "signals": self.signals, "in_production_pool": self.in_production_pool, "flags": self.flags,
                "source": self.source, "text": self.focus}


@dataclass
class Pool:
    queries: list[str]
    candidates: list[Candidate]
    query_embeddings: list[list[float]]
    timings_ms: dict[str, float]
    production_pool_size: int


def build_queries(question: str, mode: str, understanding: dict | None) -> list[str]:
    """The retrieval queries for a query mode. Recorded verbatim with every result."""
    if mode == "original":
        return [question]
    if mode == "rewrite":
        rw = rewrite_query(question)
        return [question] + ([rw] if rw and rw.lower() != question.lower() else [])
    u = understanding or {}
    topic = str(u.get("topic") or "").strip()
    if mode == "qu_rewrite":
        # Production's rewrite, steered by the understood topic and the components the answer needs.
        hint = "; ".join(x for x in [topic, ", ".join(u.get("required_components") or [])] if x)
        rw = rewrite_query(question, context_hint=hint[:300])
        return [question] + ([rw] if rw and rw.lower() != question.lower() else [])
    if mode == "multi":
        # Original + production rewrite + at most two component queries. Deliberately small: every extra
        # query is two more searches and, worse, more candidates competing for the same pool.
        rw = rewrite_query(question)
        qs = [question] + ([rw] if rw and rw.lower() != question.lower() else [])
        for comp in (u.get("required_components") or [])[:2]:
            q = f"{topic} {comp}".strip()
            if q and q.lower() not in {x.lower() for x in qs}:
                qs.append(q)
        return qs[:4]
    raise ValueError(mode)


def retrieve(session, queries: list[str], pool_size: int, rrf_k: int = 60,
             w_vector: float = 1.0, w_keyword: float = 1.0, list_limit: int = CANDIDATE_LIMIT) -> Pool:
    timings: dict[str, float] = {}
    rrf: dict[int, float] = {}
    best_child: dict[int, str] = {}
    signals: dict[int, dict[str, Any]] = {}
    heads: list[int] = []
    embeddings: list[list[float]] = []
    t = time.perf_counter()
    for qi, q in enumerate(queries):
        t_e = time.perf_counter()
        emb = retrieval_service._embed_query(q)
        timings["embed_ms"] = timings.get("embed_ms", 0) + (time.perf_counter() - t_e) * 1000
        embeddings.append(emb)
        t_v = time.perf_counter()
        vec = retrieval_service.vector_search(session, emb, limit=list_limit)
        timings["vector_ms"] = timings.get("vector_ms", 0) + (time.perf_counter() - t_v) * 1000
        t_k = time.perf_counter()
        kw = retrieval_service.keyword_search(session, q, limit=list_limit)
        timings["keyword_ms"] = timings.get("keyword_ms", 0) + (time.perf_counter() - t_k) * 1000
        for name, results, weight in (("vector", vec, w_vector), ("keyword", kw, w_keyword)):
            head: list[int] = []
            for rank, (child, score) in enumerate(results, 1):
                pid = child.parent_id or child.id
                rrf[pid] = rrf.get(pid, 0.0) + weight / (rrf_k + rank)
                signals.setdefault(pid, {}).setdefault(f"{name}_q{qi}", {"rank": rank, "score": round(float(score), 5)})
                if pid not in best_child:
                    best_child[pid] = (child.extra_metadata or {}).get("original_text") or child.content
                if len(head) < PER_LIST_TOP and pid not in head:
                    head.append(pid)
            heads.extend(head)
    ordered = sorted(rrf, key=rrf.get, reverse=True)
    production_ids = set(dict.fromkeys(ordered[:16] + heads))
    wanted = list(dict.fromkeys(ordered[:pool_size + 40] + heads))
    t_p = time.perf_counter()
    parents = session.query(Chunk).filter(Chunk.id.in_(wanted)).options(joinedload(Chunk.book)).all()
    timings["fetch_ms"] = (time.perf_counter() - t_p) * 1000
    pmap = {p.id: p for p in parents if not looks_like_index_page(p.content)}
    pool_ids = [pid for pid in ordered if pid in pmap][:pool_size]
    pool_ids += [pid for pid in dict.fromkeys(heads) if pid in pmap and pid not in pool_ids]
    rank_of = {pid: i for i, pid in enumerate(ordered, 1)}
    cands = [Candidate(chunk=pmap[pid], focus=_focus_text(pmap[pid].content, best_child.get(pid)),
                       best_child=best_child.get(pid) or "", rrf=rrf[pid], rrf_rank=rank_of[pid],
                       signals=signals.get(pid, {}), in_production_pool=pid in production_ids)
             for pid in pool_ids]
    timings["retrieval_total_ms"] = (time.perf_counter() - t) * 1000
    return Pool(queries, cands, embeddings, {k: round(v, 1) for k, v in timings.items()},
                len([p for p in production_ids if p in pmap]))


# ── experimental chunk-quality filter ─────────────────────────────────────────

_BIB = [re.compile(p, re.I) for p in (
    r"\bet al\.?", r"\b(19|20)\d{2}\s*;\s*\d+", r"\bdoi\s*[:.]", r"\b(N Engl J Med|Lancet|JAMA|BMJ|Circulation|Pediatrics)\b",
    r"^\s*from\s+[A-Z][a-z]+\s+[A-Z]{1,3}\b", r"\bpublished correction\b", r"\bscientific statement\b",
)]
_TOC_LINE = re.compile(r"(\.{3,}|\s{2,})\s*\d{1,4}\s*$")


def quality_flags(text: str) -> list[str]:
    """Why a chunk is unlikely to answer anything. Empty list = looks like real prose or a table."""
    t = (text or "").strip()
    flags = []
    words = len(t.split())
    if looks_like_index_page(t):
        flags.append("index")
    if words <= 8 or (len(t) < 80 and not re.search(r"[.:;]\s|[.:;]$", t)):
        flags.append("heading_only")
    elif len(t) < 150:
        flags.append("very_short")
    if sum(bool(p.search(t)) for p in _BIB) >= 2:
        flags.append("bibliography")
    lines = [ln for ln in t.splitlines() if ln.strip()]
    if len(lines) >= 4 and sum(bool(_TOC_LINE.search(ln)) for ln in lines) >= 3:
        flags.append("table_of_contents")
    return flags


def apply_quality_flags(cands: list[Candidate]) -> None:
    for c in cands:
        c.flags = quality_flags(c.chunk.content)
