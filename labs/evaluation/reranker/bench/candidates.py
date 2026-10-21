"""Stage 1: the candidate set for every item, built with production's own retrieval primitives.

Reproduces the first half of `RetrievalService.search()` (backend/app/retrieval.py) up to the point
where it calls the cross-encoder, but keeps the top POOL_SIZE parents by RRF instead of 16:

    queries   = [query, rewrite_query(query)]                       (rewrite recorded, then replayed)
    per query = vector_search(CANDIDATE_LIMIT) + keyword_search(CANDIDATE_LIMIT)
    RRF       = sum 1/(60 + rank) over the four lists, keyed by parent id
    pool      = top POOL_SIZE parents by RRF  +  the top PER_LIST_TOP of every list
                (index pages dropped, as production does)
    text      = _focus_text(parent, best matching child)   - what production's rerankers score

Nothing here writes to the database. `verify_pool.py` checks that this pool contains everything the
real search() hands to its reranker.

Each candidate also records the metadata the structured-context experiment may use (book, chapter
path, page, headings), and each item records the diagnostics the failure analysis needs:
  * corpus_matches  how many parents in the whole library satisfy the gold predicate
  * split_answer    the predicate is met by no single candidate but by a candidate plus its
                    same-page neighbours (a chunking failure)
  * contest pool    for confusion / terminology tests, real passages for the correct concept and
                    each distractor are added when retrieval did not surface them
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .common import CACHE, Gold, Item, read_json, use_backend, write_json

use_backend()

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import joinedload  # noqa: E402

from app.models import Chunk  # noqa: E402
from app.retrieval import (  # noqa: E402
    CANDIDATE_LIMIT, PER_LIST_TOP, RERANK_POOL, _focus_text, looks_like_index_page, retrieval_service,
    rewrite_query,
)

log = logging.getLogger("bench.candidates")

POOL_SIZE = 50
RRF_K = 60                 # production: 1.0 / (60 + rank)
INJECT_PER_SIDE = 2        # contest tests: real passages added per concept when retrieval missed them
CORPUS_CAP = 500           # stop counting corpus matches here (only zero vs some matters)
NEIGHBOUR_WINDOW = 3       # production's expand_context window

REWRITES = CACHE / "rewrites.json"
CANDIDATES = CACHE / "candidates.json"


def _rewrite(query: str, cache: dict[str, Any], refresh: bool) -> str | None:
    """Production's rewrite, called once per query and replayed afterwards (it is an LLM call)."""
    if query in cache and not refresh:
        return cache[query]["rewrite"]
    t0 = time.monotonic()
    out = rewrite_query(query)
    cache[query] = {"rewrite": out, "seconds": round(time.monotonic() - t0, 2)}
    write_json(REWRITES, cache)
    return out


def _meta(parent: Chunk) -> dict[str, Any]:
    em = parent.extra_metadata or {}
    return {
        "book_id": parent.book_id,
        "book": parent.book.title if parent.book else None,
        "book_subject": parent.book.subject if parent.book else None,
        "chapter": parent.chapter,
        "page": parent.page_number,
        "headings": list(em.get("headings") or []),
        "is_table": bool(em.get("is_table")),
    }


def build_pool(session, query: str, rewrite: str | None) -> dict[str, Any]:
    """The candidate pool for one query, with every retrieval signal kept."""
    queries = [query] + ([rewrite] if rewrite and rewrite.lower() != query.lower() else [])
    rrf: dict[int, float] = {}
    best_child: dict[int, str] = {}
    signals: dict[int, dict[str, Any]] = {}
    list_heads: list[int] = []
    embeddings = []
    for qi, q in enumerate(queries):
        emb = retrieval_service._embed_query(q)
        embeddings.append(emb)
        lists = (
            ("vector", retrieval_service.vector_search(session, emb, limit=CANDIDATE_LIMIT)),
            ("keyword", retrieval_service.keyword_search(session, q, limit=CANDIDATE_LIMIT)),
        )
        for name, results in lists:
            head: list[int] = []
            for rank, (child, score) in enumerate(results, 1):
                pid = child.parent_id or child.id
                rrf[pid] = rrf.get(pid, 0.0) + 1.0 / (RRF_K + rank)
                sig = signals.setdefault(pid, {})
                key = f"{name}_q{qi}"
                if key not in sig:   # a parent's best child in this list
                    sig[key] = {"rank": rank, "score": round(float(score), 5)}
                if pid not in best_child:
                    best_child[pid] = (child.extra_metadata or {}).get("original_text") or child.content
                if len(head) < PER_LIST_TOP and pid not in head:
                    head.append(pid)
            list_heads.extend(head)

    ordered = sorted(rrf, key=rrf.get, reverse=True)
    # What production would send to its reranker: the top RERANK_POOL ids (chosen before index pages
    # are dropped, as search() does) plus every list head.
    production_ids = list(dict.fromkeys(ordered[:RERANK_POOL] + list_heads))
    wanted = list(dict.fromkeys(ordered[:POOL_SIZE + 40] + list_heads))     # slack for dropped index pages
    parents = session.query(Chunk).filter(Chunk.id.in_(wanted)).options(joinedload(Chunk.book)).all()
    pmap = {p.id: p for p in parents if not looks_like_index_page(p.content)}
    pool_ids = [pid for pid in ordered if pid in pmap][:POOL_SIZE]
    pool_ids += [pid for pid in dict.fromkeys(list_heads) if pid in pmap and pid not in pool_ids]

    rrf_rank = {pid: i for i, pid in enumerate(ordered, 1)}
    candidates = []
    for pid in pool_ids:
        p = pmap[pid]
        candidates.append({
            "id": pid,
            **_meta(p),
            "rrf": round(rrf[pid], 6),
            "rrf_rank": rrf_rank[pid],
            "signals": signals.get(pid, {}),
            "in_production_pool": pid in production_ids,
            "list_head": pid in list_heads,
            "text": _focus_text(p.content, best_child.get(pid)),
            "parent_text": p.content,
            "source": "retrieval",
        })
    return {"queries": queries, "candidates": candidates, "embedding": embeddings[0],
            "production_pool_size": len([i for i in production_ids if i in pmap])}


def corpus_matches(session, gold: Gold, limit: int = CORPUS_CAP) -> list[int]:
    """Parent ids anywhere in the library whose text satisfies the predicate."""
    where, params = gold.sql("content")
    rows = session.execute(text(
        f"SELECT id FROM chunks WHERE parent_id IS NULL AND {where} LIMIT :lim"), {**params, "lim": limit})
    return [r[0] for r in rows]


def nearest_matches(session, ids: list[int], embedding: list[float], k: int) -> list[int]:
    """Of these parents, the k whose closest child is nearest the query (cosine)."""
    if not ids:
        return []
    rows = session.execute(text(
        "SELECT parent_id, MIN(embedding <=> CAST(:q AS vector)) AS d FROM chunks "
        "WHERE parent_id = ANY(:ids) AND embedding IS NOT NULL GROUP BY parent_id ORDER BY d LIMIT :k"),
        {"q": str(list(embedding)), "ids": ids, "k": k})
    return [r[0] for r in rows]


def split_answer(session, gold: Gold, candidates: list[dict[str, Any]]) -> list[int]:
    """Candidates whose same-page neighbourhood (production's expand window) meets a predicate the
    candidate alone does not: the answer exists, but across chunk boundaries."""
    hits = []
    for c in candidates[:POOL_SIZE]:
        if c["page"] is None or gold.matches(c["parent_text"]):
            continue
        rows = session.execute(text(
            "SELECT content FROM chunks WHERE book_id = :b AND parent_id IS NULL AND page_number = :p "
            "AND id BETWEEN :lo AND :hi ORDER BY id"),
            {"b": c["book_id"], "p": c["page"], "lo": c["id"] - NEIGHBOUR_WINDOW, "hi": c["id"] + NEIGHBOUR_WINDOW})
        if gold.matches("\n".join(r[0] for r in rows)):
            hits.append(c["id"])
    return hits


def _inject(session, item: Item, pool: dict[str, Any], label: str, gold: Gold, exclude: Gold | None) -> list[int]:
    """Add the nearest real passages for one concept if the pool has none of them."""
    present = [c for c in pool["candidates"] if gold.matches(c["parent_text"])
               and not (exclude and exclude.matches(c["parent_text"]))]
    if present:
        return []
    ids = corpus_matches(session, gold, limit=CORPUS_CAP)
    if exclude is not None:
        body = {r[0]: r[1] for r in session.execute(
            text("SELECT id, content FROM chunks WHERE id = ANY(:ids)"), {"ids": ids})}
        ids = [i for i in ids if not exclude.matches(body.get(i, ""))]
    have = {c["id"] for c in pool["candidates"]}
    chosen = [i for i in nearest_matches(session, ids, pool["embedding"], INJECT_PER_SIDE * 2) if i not in have]
    chosen = chosen[:INJECT_PER_SIDE]
    if not chosen:
        return []
    parents = session.query(Chunk).filter(Chunk.id.in_(chosen)).options(joinedload(Chunk.book)).all()
    for p in sorted(parents, key=lambda x: chosen.index(x.id)):
        # The passage's own nearest child decides its focus window, as it would in production.
        child = session.execute(text(
            "SELECT content FROM chunks WHERE parent_id = :p AND embedding IS NOT NULL "
            "ORDER BY embedding <=> CAST(:q AS vector) LIMIT 1"),
            {"p": p.id, "q": str(list(pool["embedding"]))}).scalar()
        pool["candidates"].append({
            "id": p.id, **_meta(p), "rrf": 0.0, "rrf_rank": None, "signals": {},
            "in_production_pool": False, "list_head": False,
            "text": _focus_text(p.content, child), "parent_text": p.content,
            "source": f"injected:{label}",
        })
    return chosen


def build_all(items: list[Item], refresh_rewrites: bool = False, only: set[str] | None = None,
              rebuild: bool = False) -> dict[str, Any]:
    from app.database import SessionLocal
    from app.llm import llm_configured

    rewrites = read_json(REWRITES, {}) or {}
    existing = read_json(CANDIDATES, {}) or {}
    out = {"meta": existing.get("meta", {}), "items": existing.get("items", {})}
    if not llm_configured("fast"):
        log.warning("The fast LLM is not configured: no query rewrites, unlike production.")
    session = SessionLocal()
    try:
        for n, item in enumerate(items, 1):
            if only and item.id not in only:
                continue
            if item.id in out["items"] and not rebuild and not only:
                continue          # resumable: a crash costs only the item in flight
            t0 = time.monotonic()
            rw = _rewrite(item.query, rewrites, refresh_rewrites)
            pool = build_pool(session, item.query, rw)
            gold = item.gold
            matches = corpus_matches(session, gold) if not gold.empty() else []
            entry: dict[str, Any] = {
                "queries": pool["queries"],
                "production_pool_size": pool["production_pool_size"],
                "corpus_matches": len(matches),
                "corpus_capped": len(matches) >= CORPUS_CAP,
                "verified_ids_missing": [],
                "split_answer": [],
                "injected": {},
                "retrieval_seconds": 0.0,
            }
            if item.relevant_chunk_ids:
                found = {r[0] for r in session.execute(
                    text("SELECT id FROM chunks WHERE id = ANY(:ids)"), {"ids": item.relevant_chunk_ids})}
                entry["verified_ids_missing"] = [i for i in item.relevant_chunk_ids if i not in found]
            if not gold.empty() and not any(gold.matches(c["parent_text"]) for c in pool["candidates"]):
                entry["split_answer"] = split_answer(session, gold, pool["candidates"])
            if item.kind in ("confusion", "terminology"):
                entry["injected"]["__correct__"] = _inject(session, item, pool, "correct", gold, None)
                for d in item.distractors:
                    entry["injected"][d.label] = _inject(session, item, pool, d.label, d.gold, gold)
            entry["candidates"] = pool["candidates"]
            entry["retrieval_seconds"] = round(time.monotonic() - t0, 2)
            out["items"][item.id] = entry
            session.rollback()       # SET LOCAL in vector_search is transaction-scoped
            print(f"[{n}/{len(items)}] {item.id} {len(pool['candidates']):3d} cands "
                  f"(prod pool {pool['production_pool_size']}), corpus {len(matches)}"
                  f"{'+' if len(matches) >= CORPUS_CAP else ''}, rewrite={'yes' if rw else 'no'}, "
                  f"{entry['retrieval_seconds']}s", flush=True)
            write_json(CANDIDATES, out)
    finally:
        session.close()
    out["meta"].update({"pool_size": POOL_SIZE, "rrf_k": RRF_K, "candidate_limit": CANDIDATE_LIMIT,
                        "per_list_top": PER_LIST_TOP, "production_rerank_pool": RERANK_POOL,
                        "inject_per_side": INJECT_PER_SIDE, "built_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    write_json(CANDIDATES, out)
    return out
