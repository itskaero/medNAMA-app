"""Run one question through a set of configurations, on shared inputs, and record everything.

Fairness rules:
  * one candidate pool per retrieval key (query mode, pool size, RRF settings) per run: every
    configuration with the same key reranks the identical candidates
  * one question-understanding result per run, reused by every configuration that uses it
  * the same LLM (production's) and, unless a configuration says otherwise, the same experimental prompt
  * shared work is timed once and reported on each configuration that used it, marked "shared"

CURRENT is production itself (production_runner.run_current): it runs its own retrieval, so its pool
can differ slightly from the shared one (production retrieval is not fully deterministic: full-text
score ties and approximate vector search).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from . import settings
from .configs import Config
from .resources import Meter, capture_llm_usage, install_usage_spy, summarise_usage

from app import exam_levels  # noqa: E402
from app.retrieval import INTENT_SUBJECTS, LEVEL_SUBJECT_BONUS, ContextChunk, retrieval_service, select_diverse  # noqa: E402

TARGETED_POOL = 8
TARGETED_MAX_COMPONENTS = 2


class Shared:
    """Work shared by the configurations of one run."""

    def __init__(self, session, question: str, level: str):
        self.session, self.question, self.level = session, question, level
        self.understanding: dict | None = None
        self.understanding_ms = 0.0
        self.pools: dict[tuple, Any] = {}
        self.pool_ms: dict[tuple, float] = {}

    def get_understanding(self) -> dict:
        if self.understanding is None:
            from .llm_stages import understand
            t = time.perf_counter()
            self.understanding = understand(self.question, self.level)
            self.understanding_ms = (time.perf_counter() - t) * 1000
        return self.understanding

    def get_pool(self, cfg: Config, understanding: dict | None):
        from .retrieval import apply_quality_flags, build_queries, retrieve
        key = cfg.retrieval_key()
        if key not in self.pools:
            t = time.perf_counter()
            queries = build_queries(self.question, cfg.query_mode, understanding)
            pool = retrieve(self.session, queries, cfg.pool_size, cfg.rrf_k, cfg.rrf_weight_vector, cfg.rrf_weight_keyword)
            apply_quality_flags(pool.candidates)     # always recorded; acted on only with chunk_filter
            self.pool_ms[key] = (time.perf_counter() - t) * 1000
            self.pools[key] = pool
        return self.pools[key]


# ── reranking ─────────────────────────────────────────────────────────────────

def rerank(cfg: Config, pool, level: str, question: str) -> dict[str, Any]:
    from . import rerankers

    cands = pool.candidates
    texts = [c.focus for c in cands]
    out: dict[str, Any] = {"stage1": cfg.stage1, "stage2": cfg.stage2, "stage1_ms": 0.0, "stage2_ms": 0.0}
    if cfg.stage1:
        s1, ms1 = rerankers.score(cfg.stage1, pool.queries, texts)
        out["stage1_ms"] = ms1
        order = sorted(range(len(cands)), key=lambda i: (-s1[i], i))
    else:
        s1 = [c.rrf for c in cands]
        order = list(range(len(cands)))                       # RRF order
    s2: dict[int, float] = {}
    if cfg.stage1 and cfg.stage2:
        head = order[:cfg.stage2_top_n]
        scores, ms2 = rerankers.score(cfg.stage2, pool.queries, [texts[i] for i in head])
        out["stage2_ms"] = ms2
        s2 = dict(zip(head, scores))
        order = sorted(head, key=lambda i: (-s2[i], order.index(i))) + order[cfg.stage2_top_n:]
    # Exam level as a soft preference: production's bonus (LEVEL_SUBJECT_BONUS, subjects narrowed by
    # intent) added to each passage's score in the stage that ordered it - stage 2 inside the head,
    # stage 1 below it - so the head stays ahead of the tail and stage 2's order is kept.
    # (Production differs here: it re-sorts the WHOLE list by stage-1 score + bonus, which discards
    # the second stage's ordering whenever a level is set. The lab does not copy that; CURRENT shows it.)
    bonus_applied = []
    if cfg.level_bonus and cfg.stage1:
        wanted = exam_levels.preferred_subjects(level)
        focus = INTENT_SUBJECTS.get(exam_levels.classify_intent(question), ())
        if wanted and focus and (wanted & frozenset(focus)):
            wanted = wanted & frozenset(focus)
        if wanted:
            bonus_applied = sorted(wanted)
            pos = {i: r for r, i in enumerate(order)}

            def bonused(i: int, base: float) -> tuple[float, int]:
                subj = (cands[i].chunk.book.subject if cands[i].chunk.book else "") or ""
                return -(base + (LEVEL_SUBJECT_BONUS if subj in wanted else 0.0)), pos[i]

            if s2:
                head = [i for i in order if i in s2]
                tail = [i for i in order if i not in s2]
                order = sorted(head, key=lambda i: bonused(i, s2[i])) + sorted(tail, key=lambda i: bonused(i, s1[i]))
            else:
                order = sorted(order, key=lambda i: bonused(i, s1[i]))
    out["level_bonus_subjects"] = bonus_applied
    # Experimental chunk-quality filter: flagged chunks go to the bottom (kept, never deleted).
    demoted = []
    if cfg.chunk_filter:
        good = [i for i in order if not cands[i].flags]
        bad = [i for i in order if cands[i].flags]
        demoted = [cands[i].id for i in bad]
        order = good + bad
    out["demoted_by_quality_filter"] = demoted
    out["order"] = order
    out["stage1_scores"] = s1
    out["stage2_scores"] = s2
    return out


# ── context selection ─────────────────────────────────────────────────────────

def select_context(session, cfg: Config, pool, rr: dict[str, Any]) -> tuple[list[Any], list[dict], list[int]]:
    cands = pool.candidates
    ranked = [(cands[i].chunk, float(rr["stage1_scores"][i])) for i in rr["order"]]
    if cfg.context_strategy == "production_diverse":
        chosen = select_diverse(ranked, cfg.top_k)
    else:
        chosen = ranked[:cfg.top_k]
    chosen_ids = [c.id for c, _ in chosen]
    if cfg.expand_neighbours:
        focus = {c.id: c.best_child for c in cands}
        context = retrieval_service.expand_context(session, chosen, focus=focus, queries=pool.queries)
    else:
        by_id = {c.id: c for c in cands}
        context = [ContextChunk(ch.id, ch.book_id, ch.book, ch.chapter, ch.page_number, by_id[ch.id].focus,
                                score=sc, parts={ch.id: by_id[ch.id].focus}) for ch, sc in chosen]
    figures = retrieval_service.select_figures(session, pool.query_embeddings, context) if cfg.figures else []
    return context, figures, chosen_ids


def _sources(context: list[Any]) -> list[dict[str, Any]]:
    out = []
    for rank, c in enumerate(context, 1):
        snippet = " ".join((c.content or "").split())[:300]
        out.append({"chunk_id": c.id, "book_id": c.book_id, "book_title": c.book.title if c.book else "Unknown Textbook",
                    "chapter": c.chapter, "page_number": c.page_number, "snippet": snippet, "rank": rank,
                    "relevance_score": round(float(getattr(c, "score", 0.0)), 4)})
    return out


def _context_record(context: list[Any]) -> list[dict[str, Any]]:
    return [{"id": c.id, "parts": list(getattr(c, "chunk_ids", [c.id])), "book": c.book.title if c.book else None,
             "chapter": c.chapter, "page": c.page_number, "score": round(float(getattr(c, "score", 0.0)), 4),
             "text": c.content, "source": getattr(c, "_lab_source", "retrieval")} for c in context]


# ── one configuration ─────────────────────────────────────────────────────────

def run_config(shared: Shared, cfg: Config, item: dict[str, Any], strictness: str) -> dict[str, Any]:
    from .generation import generate
    from .llm_stages import coverage as coverage_check, plan as make_plan
    from .metrics import ranking_metrics, relevance_fn, suggest_failure

    session, question, level = shared.session, shared.question, shared.level
    meter = Meter()
    rec: dict[str, Any] = {"configuration": cfg.name, "label": cfg.label, "config": cfg.to_dict(),
                           "level": level, "strictness": strictness, "question": question,
                           "question_id": item.get("id")}
    rel_fn = relevance_fn(item)
    shared_ms = 0.0          # work this configuration reused from an earlier one in the run
    with capture_llm_usage() as calls:
        try:
            if cfg.production:
                _run_production(shared, cfg, item, rec, meter, rel_fn)
            else:
                full = cfg.generation_mode == "full"
                wants_qu = cfg.understanding or full or cfg.query_mode in ("qu_rewrite", "multi")
                understanding = None
                if wants_qu:
                    reused = shared.understanding is not None
                    understanding = shared.get_understanding()
                    meter.stages["understanding_ms"] = shared.understanding_ms
                    shared_ms += shared.understanding_ms if reused else 0.0
                    rec["understanding"] = {**understanding, "_shared": reused}
                key = cfg.retrieval_key()
                reused_pool = key in shared.pools
                pool = shared.get_pool(cfg, understanding)
                meter.stages["retrieval_ms"] = shared.pool_ms[key]
                shared_ms += shared.pool_ms[key] if reused_pool else 0.0
                with meter.stage("rerank_ms"):
                    rr = rerank(cfg, pool, level, question)
                with meter.stage("context_ms"):
                    context, figures, chosen_ids = select_context(session, cfg, pool, rr)
                cov = cov_after = None
                targeted: list[dict[str, Any]] = []
                coverage_mode = "check" if (full and cfg.coverage == "none") else cfg.coverage
                if coverage_mode != "none":
                    with meter.stage("coverage_ms"):
                        required = (understanding or {}).get("required_components") or []
                        cov = coverage_check(question, level, required, context)
                    if coverage_mode == "check_retrieve" and cov.get("missing"):
                        with meter.stage("targeted_retrieval_ms"):
                            targeted = _targeted(shared, cfg, pool, context, cov, understanding)
                        if any(t.get("added") for t in targeted):
                            with meter.stage("coverage_after_ms"):
                                cov_after = coverage_check(question, level, cov.get("required") or required, context)
                plan = None
                if cfg.generation_mode in ("plan", "full"):
                    with meter.stage("plan_ms"):
                        plan = make_plan(question, level, understanding if full else None, cov if full else None, context)
                with meter.stage("generation_ms"):
                    if cfg.prompt == "production":
                        from .production_runner import with_context
                        ans = with_context(session, question, level, context, figures, _sources(context), pool.queries)
                        ans["prompt"] = {"name": "production", "note": "production generate_answer() on this context"}
                    else:
                        ans = generate(question, level, strictness, context, figures,
                                       understanding if (cfg.understanding or full) else None,
                                       cov_after or cov, plan)
                rec["retrieval"] = {"queries": pool.queries, "query_mode": cfg.query_mode, "pool_size": cfg.pool_size,
                                    "rrf_k": cfg.rrf_k, "rrf_weights": [cfg.rrf_weight_vector, cfg.rrf_weight_keyword],
                                    "number_of_candidates": len(pool.candidates), "timings_ms": pool.timings_ms,
                                    "production_pool_size": pool.production_pool_size, "shared": reused_pool}
                order = rr["order"]
                rec["reranking"] = {
                    "stage1": rr["stage1"], "stage2": rr["stage2"], "stage1_ms": round(rr["stage1_ms"], 1),
                    "stage2_ms": round(rr["stage2_ms"], 1), "level_bonus_subjects": rr["level_bonus_subjects"],
                    "demoted_by_quality_filter": rr["demoted_by_quality_filter"],
                    "ranking": [{"rank": r, **pool.candidates[i].summary(),
                                 "stage1_score": None if not cfg.stage1 else round(rr["stage1_scores"][i], 4),
                                 "stage2_score": round(rr["stage2_scores"][i], 4) if i in rr["stage2_scores"] else None}
                                for r, i in enumerate(order, 1)],
                }
                rec["context"] = _context_record(context)
                rec["final_context_count"] = len(context)
                rec["selected_parent_ids"] = chosen_ids
                rec["figures"] = figures
                rec["coverage"] = {"mode": coverage_mode, "before": cov, "after": cov_after, "targeted": targeted}
                rec["plan"] = plan
                rec["answer"] = ans
                if rel_fn:
                    rel = {c.id: rel_fn(c.id, c.chunk.content) for c in pool.candidates}
                    m = ranking_metrics([pool.candidates[i].id for i in order], rel)
                    m_retrieval = ranking_metrics([c.id for c in pool.candidates], rel)
                    ctx_rel = any(rel_fn(c.id, c.content) for c in context)
                    rec["retrieval_metrics"] = {"after_reranking": m, "retrieval_order": m_retrieval,
                                                "relevant_in_final_context": ctx_rel}
                    rec["auto_failure_suggestion"] = suggest_failure(m, ctx_rel, cfg.top_k)
                    for entry in rec["reranking"]["ranking"]:
                        entry["relevant"] = rel.get(entry["id"])
                    for c in rec["context"]:
                        c["relevant"] = rel_fn(c["id"], c["text"])
        except Exception as e:                      # one failing configuration must not lose the others
            import traceback
            rec["error"] = f"{type(e).__name__}: {e}"
            rec["traceback"] = traceback.format_exc()[-3000:]
    metrics = meter.finish()
    stages = metrics["stages_ms"]
    rer = rec.get("reranking", {})
    rec["metrics"] = {
        **metrics,
        "retrieval_ms": stages.get("retrieval_ms"),
        "rerank_ms": round((rer.get("stage1_ms") or 0) + (rer.get("stage2_ms") or 0), 1) if rer else stages.get("rerank_ms"),
        "total_retrieval_ms": round((stages.get("retrieval_ms") or 0) + (stages.get("rerank_ms") or 0)
                                    + (stages.get("context_ms") or 0), 1),
        "generation_ms": stages.get("generation_ms"),
        # As if this configuration had run alone: its own wall time plus the shared work it reused.
        # Use this to compare latency; total_ms favours whichever configuration ran later.
        "shared_reused_ms": round(shared_ms, 1),
        "effective_total_ms": round(metrics["total_ms"] + shared_ms, 1),
        "tokens": summarise_usage(calls),
    }
    return rec


def _targeted(shared: Shared, cfg: Config, pool, context: list[Any], cov: dict, understanding: dict | None):
    """A second, narrow retrieval per missing component; the best new passage joins the context."""
    from .retrieval import retrieve
    from . import rerankers

    topic = (understanding or {}).get("topic") or shared.question
    have = {pid for c in context for pid in getattr(c, "chunk_ids", [c.id])}
    out = []
    for comp in cov.get("missing", [])[:TARGETED_MAX_COMPONENTS]:
        q = f"{topic} {comp}".strip()
        sub = retrieve(shared.session, [q], TARGETED_POOL, cfg.rrf_k, cfg.rrf_weight_vector, cfg.rrf_weight_keyword,
                       list_limit=20)
        cands = [c for c in sub.candidates if c.id not in have]
        entry: dict[str, Any] = {"component": comp, "query": q, "candidates": len(cands), "added": None}
        if cands:
            if cfg.stage1:
                scores, _ = rerankers.score(cfg.stage2 or cfg.stage1, [q], [c.focus for c in cands])
                best = max(range(len(cands)), key=lambda i: scores[i])
                entry["score"] = round(scores[best], 4)
            else:
                best = 0
            b = cands[best]
            block = ContextChunk(b.chunk.id, b.chunk.book_id, b.chunk.book, b.chunk.chapter, b.chunk.page_number,
                                 b.focus, score=0.0, parts={b.chunk.id: b.focus})
            block._lab_source = f"targeted:{comp}"
            context.append(block)
            have.add(b.chunk.id)
            entry["added"] = {"id": b.chunk.id, "book": b.chunk.book.title if b.chunk.book else None,
                              "page": b.chunk.page_number}
        out.append(entry)
    return out


def _run_production(shared: Shared, cfg: Config, item: dict[str, Any], rec: dict[str, Any], meter: Meter, rel_fn) -> None:
    from .metrics import ranking_metrics
    from .production_runner import run_current
    from app.models import Chunk

    session = shared.session
    with meter.stage("production_total_ms"):
        answer, seen = run_current(session, shared.question, shared.level)
    res = seen.get("result")
    pool_ids = seen.get("pool") or []
    meter.stages["retrieval_ms"] = round(max(0.0, seen.get("search_ms", 0) - seen["stage1_ms"] - seen["stage2_ms"]), 1)
    meter.stages["generation_ms"] = round(meter.stages["production_total_ms"] - seen.get("search_ms", 0), 1)
    rec["retrieval"] = {"queries": res.queries if res else [], "query_mode": "production",
                        "pool_size": len(pool_ids), "number_of_candidates": len(pool_ids),
                        "search_kwargs": seen.get("search_kwargs"), "shared": False,
                        "note": "production's own search (level probes and heads included when a level is set)"}
    rec["reranking"] = {"stage1": "msmarco", "stage2": "medcpt (production setting)",
                        "stage1_ms": round(seen["stage1_ms"], 1), "stage2_ms": round(seen["stage2_ms"], 1),
                        "ranking": [{"rank": s["rank"], "id": s["chunk_id"], "book": s["book_title"],
                                     "page": s["page_number"], "chapter": s["chapter"],
                                     "stage1_score": s["relevance_score"], "text": s["snippet"]}
                                    for s in (res.sources if res else [])],
                        "pool_ids": pool_ids}
    context = res.context if res else []
    rec["context"] = _context_record(context)
    rec["final_context_count"] = len(context)
    rec["figures"] = res.figures if res else []
    rec["coverage"] = {"mode": "none"}
    rec["plan"] = None
    rec["answer"] = {**answer, "prompt": {"name": "production", "note": "production generate_answer(), unchanged"}}
    if rel_fn and pool_ids:
        texts = {c.id: c.content for c in session.query(Chunk).filter(Chunk.id.in_(pool_ids)).all()}
        rel = {pid: rel_fn(pid, texts.get(pid, "")) for pid in pool_ids}
        order = [s["chunk_id"] for s in (res.sources if res else [])]
        order += [p for p in pool_ids if p not in order]      # beyond the 8 sources production reports
        m = ranking_metrics(order, rel)
        ctx_rel = any(rel_fn(c.id, c.content) for c in context)
        rec["retrieval_metrics"] = {"after_reranking": m, "relevant_in_final_context": ctx_rel,
                                    "note": "ranks beyond production's 8 reported sources are pool order, not reranked order"}
        rec["auto_failure_suggestion"] = suggest_failure_safe(m, ctx_rel, 6)
        for entry in rec["reranking"]["ranking"]:
            entry["relevant"] = rel.get(entry["id"])
        for c in rec["context"]:
            c["relevant"] = rel_fn(c["id"], c["text"])


def suggest_failure_safe(m, ctx_rel, k):
    from .metrics import suggest_failure
    return suggest_failure(m, ctx_rel, k)


# ── a whole run ───────────────────────────────────────────────────────────────

def reproducibility(configs: list[Config]) -> dict[str, Any]:
    from . import generation, llm_stages, rerankers
    from app.config import settings as app_settings
    from app.llm import endpoint

    used = {m for c in configs for m in (c.stage1, c.stage2) if m}
    return {
        "lab_version": settings.LAB_VERSION, "git": settings.git_state(), "device": settings.DEVICE,
        "models": {k: settings.MODELS[k] for k in sorted(used)},
        "model_load_info": rerankers.load_info(),
        "embedding_model": "BAAI/bge-large-en-v1.5 (production get_embedding_model)",
        "production_second_stage": app_settings.reranker_second_stage,
        "production_second_stage_top_n": app_settings.reranker_second_stage_top_n,
        "llm": {"chat": endpoint("chat").model, "fast": endpoint("fast").model, "analysis_role": settings.ANALYSIS_ROLE,
                "temperature": 0.0},
        "prompts": {"generation": generation.PROMPT_VERSION, "generation_hash": generation.prompt_hash(),
                    **llm_stages.PROMPT_VERSIONS},
    }


_warm: set[str] = set()


def warm_up(session, configs: list[Config]) -> dict[str, Any]:
    """Load every model the configurations need, and touch the database, before anything is timed.

    Without this the first configuration of a session pays the embedding-model load, production's own
    ms-marco and MedCPT loads, and cold database caches (measured: CURRENT's retrieval took 177 s
    instead of ~15 s when it ran first). Model RAM is recorded per model in rerankers.load_info()."""
    from . import rerankers
    from app.ingestion import get_embedding_model
    from app.retrieval import get_reranker_model, get_second_stage_reranker

    t = time.perf_counter()
    done = []
    if "embedding" not in _warm:
        get_embedding_model()
        retrieval_service._embed_query("warm up")
        _warm.add("embedding"); done.append("embedding")
    if any(c.production for c in configs) and "production" not in _warm:
        get_reranker_model().predict([("warm up", "warm up")])
        second = get_second_stage_reranker()
        if second is not None:
            second.predict([("warm up", "warm up")])
        _warm.add("production"); done.append("production rerankers")
    for key in sorted({m for c in configs for m in (c.stage1, c.stage2) if m} - _warm):
        rerankers.score(key, ["warm up"], ["warm up"])
        _warm.add(key); done.append(key)
    if "db" not in _warm:
        retrieval_service.keyword_search(session, "warm up", limit=5)
        _warm.add("db"); done.append("database")
    return {"loaded": done, "ms": round((time.perf_counter() - t) * 1000, 1)}


def run_question(item: dict[str, Any], level: str, strictness: str, configs: list[Config],
                 progress=None, ai_judge: bool = False) -> dict[str, Any]:
    from app.database import SessionLocal
    from . import store

    install_usage_spy()
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    question = item["question"]
    session = SessionLocal()
    shared = Shared(session, question, level)
    results = []
    if progress:
        progress("warming up models")
    warm = warm_up(session, configs)
    try:
        for i, cfg in enumerate(configs, 1):
            if progress:
                progress(f"{i}/{len(configs)} {cfg.label}")
            rec = run_config(shared, cfg, item, strictness)
            rec["run_id"] = run_id
            rec["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            results.append(rec)
            store.save_result(run_id, rec)
            if ai_judge and not rec.get("error"):
                from .llm_stages import judge
                ctx = "\n\n".join(f"[{c['book']}, Page {c['page']}]\n{c['text']}" for c in rec.get("context", []))
                store.save_ai_eval(run_id, rec["configuration"], judge(
                    question, level, rec["answer"].get("answer_markdown", ""), ctx,
                    item.get("expected_components"), item.get("gold_notes")))
    finally:
        session.close()
    meta = {"run_id": run_id, "question_id": item.get("id"), "question": question, "level": level,
            "strictness": strictness, "item": item, "configurations": [c.name for c in configs],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "reproducibility": reproducibility(configs),
            "shared_understanding": shared.understanding, "warm_up": warm}
    store.save_meta(run_id, meta)
    return {"meta": meta, "results": results}
