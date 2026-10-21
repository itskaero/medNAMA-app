"""Stage 3: rankings for every arm, metrics, contests, failure analysis, results.json / results.csv.

Arms (all rank the SAME frozen pool):

    retrieval           RRF order from retrieval, no cross-encoder (the floor)
    current             production: ms-marco orders the pool, MedCPT re-orders its top 8
    msmarco             production stage 1 alone (what background jobs use)
    medcpt              MedCPT over the whole pool
    bge_v2_m3           BGE v2-m3 over the whole pool
    bge_base            BGE base over the whole pool
    msmarco>bge_base@8  production's shape with BGE base as stage 2
    msmarco>bge_v2_m3@8 production's shape with BGE v2-m3 as stage 2
    medcpt+bge_v2_m3    reciprocal-rank fusion of the two full rankings (a hybrid)

Relevance: a verified item uses its verified chunk ids; every other item uses its gold predicate on
the candidate's parent text. Each result says which basis it used.

Metrics on the natural pool (retrieval candidates only; injected contest passages excluded):
    Hit@k       a relevant passage is in the top k  (what single-answer QA usually calls Recall@k)
    Recall@k    share of the pool's relevant passages that are in the top k
    MRR         1 / rank of the first relevant passage (0 if none)
    nDCG@k      binary relevance, ideal = min(k, relevant in pool)
"""

from __future__ import annotations

import csv
import math
import random
import statistics
from typing import Any

from .common import BENCH_DIR, Item, write_json
from .rerankers import MODELS, MODES, load_scores

KS = (1, 3, 5, 6, 8, 10, 20, 50)
CONTEXT_K = 5              # production sends 5-6 blocks to the LLM; a relevant passage below 5 is a miss
STAGE2_TOP_N = 8           # settings.reranker_second_stage_top_n
UNCERTAIN_SHARE = 0.6      # a predicate matching most of the pool is too loose to discriminate
STRICT_MAX_RELEVANT = 5    # "strict" subset: answerable questions with at most this many relevant passages
FUSION_K = 60

ARMS: dict[str, dict[str, Any]] = {
    "retrieval": {"kind": "rrf"},
    "current": {"kind": "two_stage", "first": "msmarco", "second": "medcpt"},
    "msmarco": {"kind": "single", "model": "msmarco"},
    "medcpt": {"kind": "single", "model": "medcpt"},
    "bge_v2_m3": {"kind": "single", "model": "bge_v2_m3"},
    "bge_base": {"kind": "single", "model": "bge_base"},
    "msmarco>bge_base@8": {"kind": "two_stage", "first": "msmarco", "second": "bge_base"},
    "msmarco>bge_v2_m3@8": {"kind": "two_stage", "first": "msmarco", "second": "bge_v2_m3"},
    "medcpt+bge_v2_m3": {"kind": "fusion", "models": ["medcpt", "bge_v2_m3"]},
    # The same rankings restricted to production's real rerank pool (top 16 by RRF + list heads, about 16
    # candidates instead of 50): answers "how many candidates should be reranked?".
    "current@prod_pool": {"kind": "pool", "pool": "production", "base": "current"},
    "msmarco>bge_base@8@prod_pool": {"kind": "pool", "pool": "production", "base": "msmarco>bge_base@8"},
    "bge_v2_m3@prod_pool": {"kind": "pool", "pool": "production", "base": "bge_v2_m3"},
}
HEADLINE = ["retrieval", "current", "msmarco", "medcpt", "bge_v2_m3", "bge_base"]


# ── rankings ──────────────────────────────────────────────────────────────────

def _order(scores: list[float]) -> list[int]:
    return sorted(range(len(scores)), key=lambda i: (-scores[i], i))


def rank_arm(arm: str, mode: str, item_id: str, n: int, scores: dict, rrf: list[float],
             subset: list[int] | None = None) -> list[int] | None:
    """Candidate indices in rank order. `subset` ranks only those candidates: cross-encoder scores are
    per passage, so ranking a smaller pool from the same scores is exact (no re-scoring needed)."""
    spec = ARMS[arm]
    idx = list(range(n)) if subset is None else list(subset)
    order = lambda s: sorted(idx, key=lambda i: (-s[i], i))  # noqa: E731
    get = lambda m: (scores.get((m, mode)) or {}).get(item_id, {}).get("scores")  # noqa: E731
    if spec.get("pool"):
        return rank_arm(spec["base"], mode, item_id, n, scores, rrf, subset)
    if spec["kind"] == "rrf":
        return order(rrf)
    if spec["kind"] == "single":
        s = get(spec["model"])
        return order(s) if s and len(s) == n else None
    if spec["kind"] == "two_stage":
        s1, s2 = get(spec["first"]), get(spec["second"])
        if not s1 or not s2:
            return None
        first = order(s1)
        head, tail = first[:STAGE2_TOP_N], first[STAGE2_TOP_N:]
        return sorted(head, key=lambda i: (-s2[i], first.index(i))) + tail
    if spec["kind"] == "fusion":
        models = [get(m) for m in spec["models"]]
        if any(o is None for o in models):
            return None
        fused = [0.0] * n
        for s in models:
            for r, i in enumerate(order(s), 1):
                fused[i] += 1.0 / (FUSION_K + r)
        return order(fused)
    raise ValueError(arm)


# ── metrics ───────────────────────────────────────────────────────────────────

def ranking_metrics(order: list[int], rel: list[bool]) -> dict[str, Any]:
    n_rel = sum(rel)
    ranks = [r for r, i in enumerate(order, 1) if rel[i]]
    first = ranks[0] if ranks else None
    m: dict[str, Any] = {"rank_first": first, "n_relevant": n_rel}
    for k in KS:
        m[f"hit@{k}"] = int(first is not None and first <= k)
        m[f"recall@{k}"] = (sum(1 for r in ranks if r <= k) / n_rel) if n_rel else 0.0
    m["mrr"] = 1.0 / first if first else 0.0
    for k in (5, 10):
        dcg = sum(1.0 / math.log2(r + 1) for r in ranks if r <= k)
        ideal = sum(1.0 / math.log2(r + 1) for r in range(1, min(k, n_rel) + 1))
        m[f"ndcg@{k}"] = dcg / ideal if ideal else 0.0
    return m


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(rows)}
    if not rows:
        return out
    for key in [f"hit@{k}" for k in KS] + [f"recall@{k}" for k in KS] + ["mrr", "ndcg@5", "ndcg@10"]:
        out[key] = mean([r[key] for r in rows])
    return out


def paired_bootstrap(a: list[float], b: list[float], n: int = 2000, seed: int = 0) -> dict[str, float]:
    """Mean of (a - b) with a 95% percentile interval, resampling questions."""
    if not a:
        return {"diff": float("nan"), "lo": float("nan"), "hi": float("nan"), "wins": 0, "losses": 0, "ties": 0}
    rng = random.Random(seed)
    d = [x - y for x, y in zip(a, b)]
    boots = sorted(mean([d[rng.randrange(len(d))] for _ in d]) for _ in range(n))
    return {"diff": mean(d), "lo": boots[int(0.025 * n)], "hi": boots[int(0.975 * n) - 1],
            "wins": sum(x > 1e-12 for x in d), "losses": sum(x < -1e-12 for x in d),
            "ties": sum(abs(x) <= 1e-12 for x in d)}


# ── labels, contests, failures ────────────────────────────────────────────────

def label(item: Item, cands: list[dict[str, Any]]) -> tuple[list[bool], list[bool], str]:
    """(relevant by parent text, relevant by the focus text the reranker sees, basis)."""
    if item.verified:
        ids = set(item.relevant_chunk_ids)
        rel = [c["id"] in ids for c in cands]
        return rel, rel, "verified"
    return ([item.gold.matches(c["parent_text"]) for c in cands],
            [item.gold.matches(c["text"]) for c in cands], "predicate")


def contest(item: Item, order: list[int], cands: list[dict[str, Any]]) -> dict[str, Any]:
    """Does the correct concept outrank every competing concept present in this pool?"""
    pos = {i: r for r, i in enumerate(order, 1)}
    # Only the candidates in this ranking: the natural ranking leaves the injected passages out.
    correct = [i for i, c in enumerate(cands) if i in pos and item.gold.matches(c["parent_text"])]
    best_correct = min((pos[i] for i in correct), default=None)
    per: dict[str, Any] = {}
    for d in item.distractors:
        idx = [i for i, c in enumerate(cands) if i in pos and d.gold.matches(c["parent_text"]) and i not in correct]
        best = min((pos[i] for i in idx), default=None)
        per[d.label] = {"best_rank": best, "present": best is not None,
                        "correct_wins": None if best is None or best_correct is None else best_correct < best}
    present = [v for v in per.values() if v["present"]]
    top5 = order[:5]
    return {
        "correct_rank": best_correct,
        "distractors": per,
        "contested": bool(present) and best_correct is not None,
        "pass": best_correct is not None and all(v["correct_wins"] for v in present),
        "top1_correct": bool(order) and order[0] in correct,
        "exact_p@5": (sum(1 for i in top5 if i in correct) / len(top5)) if top5 else 0.0,
        "near_miss_above": any(v["present"] and best_correct is not None and v["best_rank"] < best_correct
                               for v in per.values()),
    }


def classify(item: Item, entry: dict[str, Any], m: dict[str, Any], rel: list[bool], rel_focus: list[bool],
             cands: list[dict[str, Any]], order: list[int]) -> str | None:
    """Where a miss happened. None when a relevant passage made the top CONTEXT_K."""
    if m["rank_first"] is not None and m["rank_first"] <= CONTEXT_K:
        return None
    if entry.get("verified_ids_missing") or (not item.verified and entry.get("corpus_matches", 0) == 0):
        return "ground_truth"
    if not any(rel) or m["rank_first"] is None:
        # (rank_first is None with a relevant passage in the pool: it lies outside the smaller pool this
        # arm ranks, i.e. production's retrieval never handed it to the reranker)
        return "chunking" if entry.get("split_answer") else "retriever"
    first = order[m["rank_first"] - 1]
    c = cands[first]
    if not (c.get("chapter") or "").strip() or c.get("page") is None:
        return "metadata"
    if not any(f for f, r in zip(rel_focus, rel) if r):
        return "chunking"          # the answer is in the parent but outside the window the reranker reads
    return "reranker"


# ── the whole evaluation ──────────────────────────────────────────────────────

def evaluate(items: list[Item], candidates: dict[str, Any], device: str = "cuda") -> dict[str, Any]:
    scores: dict[tuple[str, str], dict] = {}
    meta_models: dict[str, Any] = {}
    for key in MODELS:
        for mode in MODES:
            s = load_scores(key, mode, device)
            if s:
                scores[(key, mode)] = s["items"]
                meta_models[f"{key}/{mode}"] = s["meta"]
    head_lat = (load_scores("_heads", "passage", device) or {}).get("items", {})

    results = []
    for item in items:
        entry = candidates["items"].get(item.id)
        if not entry:
            continue
        cands_all = entry["candidates"]
        natural = [i for i, c in enumerate(cands_all) if c["source"] == "retrieval"]
        rel_all, rel_focus_all, basis = label(item, cands_all)
        n_rel_pool = sum(rel_all[i] for i in natural)
        uncertain = (not item.verified and n_rel_pool >= 15 and n_rel_pool / max(1, len(natural)) > UNCERTAIN_SHARE)
        res: dict[str, Any] = {
            "id": item.id, "kind": item.kind, "query": item.query, "subject": item.subject,
            "difficulty": item.difficulty, "tags": item.tags, "queries": entry["queries"],
            "label_basis": basis, "gold": item.gold.describe(), "relevant_chunk_ids": item.relevant_chunk_ids,
            "corpus_matches": entry.get("corpus_matches"), "ground_truth_uncertain": uncertain,
            "relevant_in_pool": [cands_all[i]["id"] for i in natural if rel_all[i]],
            "split_answer": entry.get("split_answer", []),
            "n_candidates": len(natural), "n_injected": len(cands_all) - len(natural),
            "candidates": [{k: c[k] for k in ("id", "book", "book_subject", "chapter", "page", "rrf", "rrf_rank",
                                              "signals", "in_production_pool", "source")}
                           for c in cands_all],
            "original_ranking": [cands_all[i]["id"] for i in natural],
            "arms": {},
        }
        for arm in ARMS:
            for mode in MODES:
                if arm == "retrieval" and mode == "structured":
                    continue
                n_all = len(cands_all)
                prod_only = ARMS[arm].get("pool") == "production"
                subset = [i for i in natural if cands_all[i]["in_production_pool"]] if prod_only else None
                full = rank_arm(arm, mode, item.id, n_all, scores, [c["rrf"] for c in cands_all], subset)
                if full is None:
                    continue
                order = [i for i in full if i in set(natural)]
                rel = [rel_all[i] for i in range(n_all)]
                # metrics over the natural pool, with candidate indices remapped
                nat_index = {i: j for j, i in enumerate(natural)}
                m = ranking_metrics([nat_index[i] for i in order], [rel[i] for i in natural])
                failure = classify(item, entry, m, [rel[i] for i in natural], [rel_focus_all[i] for i in natural],
                                   [cands_all[i] for i in natural], [nat_index[i] for i in order]) \
                    if item.kind != "terminology" else None
                arm_scores = None
                spec = ARMS[arm]
                if spec["kind"] == "single":
                    arm_scores = scores[(spec["model"], mode)][item.id]["scores"]
                rec: dict[str, Any] = {
                    "ranking": [cands_all[i]["id"] for i in order],
                    "scores": [arm_scores[i] for i in order] if arm_scores else None,
                    **m,
                    "failure": failure,
                    "latency_s": latency(arm, mode, item.id, scores, head_lat),
                }
                if item.kind in ("confusion", "terminology") and not prod_only:
                    rec["contest_natural"] = contest(item, order, cands_all)
                    rec["contest_controlled"] = contest(item, full, cands_all)
                res["arms"].setdefault(arm, {})[mode] = rec
        results.append(res)
    return {"results": results, "models": meta_models}


def latency(arm: str, mode: str, item_id: str, scores: dict, heads: dict) -> float | None:
    spec = ARMS[arm]
    if spec.get("pool"):
        return None
    sec = lambda m: (scores.get((m, mode)) or {}).get(item_id, {}).get("seconds")  # noqa: E731
    if spec["kind"] == "rrf":
        return 0.0
    if spec["kind"] == "single":
        return sec(spec["model"])
    if spec["kind"] == "two_stage":
        first = sec(spec["first"])
        second = (heads.get(spec["second"]) or {}).get(item_id) if mode == "passage" else None
        return None if first is None or second is None else first + second
    if spec["kind"] == "fusion":
        parts = [sec(m) for m in spec["models"]]
        return None if None in parts else sum(parts)
    return None


# ── summaries ─────────────────────────────────────────────────────────────────

def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    qs = [r for r in results if r["kind"] == "question"]
    summary: dict[str, Any] = {"overall": {}, "answerable": {}, "strict": {}, "by_subject": {}, "by_difficulty": {},
                               "by_tag": {}, "failures": {}, "bootstrap_vs_current": {}, "contests": {},
                               "latency": {}}

    def rows(subset, arm, mode):
        return [r["arms"][arm][mode] for r in subset if arm in r["arms"] and mode in r["arms"][arm]]

    for arm in ARMS:
        for mode in MODES:
            key = f"{arm}|{mode}"
            rs = rows(qs, arm, mode)
            if not rs:
                continue
            summary["overall"][key] = aggregate(rs)
            summary["answerable"][key] = aggregate([x for x in rs if x["n_relevant"] > 0])
            # Loose predicates (many passages qualify) make a hit easy for every model; the strict subset
            # is where a reranker has to find a needle.
            summary["strict"][key] = aggregate([x for x in rs if 0 < x["n_relevant"] <= STRICT_MAX_RELEVANT])
            for field, bucket in (("subject", "by_subject"), ("difficulty", "by_difficulty")):
                for val in sorted({r[field] for r in qs}):
                    summary[bucket].setdefault(val, {})[key] = aggregate(rows([r for r in qs if r[field] == val], arm, mode))
            for tag in sorted({t for r in qs for t in r["tags"]}):
                summary["by_tag"].setdefault(tag, {})[key] = aggregate(rows([r for r in qs if tag in r["tags"]], arm, mode))
            fails: dict[str, int] = {}
            for r in [r for r in results if r["kind"] in ("question", "confusion")]:
                rec = r["arms"].get(arm, {}).get(mode)
                if rec and rec["failure"]:
                    fails[rec["failure"]] = fails.get(rec["failure"], 0) + 1
            summary["failures"][key] = fails
            lats = [x["latency_s"] for x in rows(results, arm, mode) if x["latency_s"] is not None]
            if lats:
                summary["latency"][key] = {"mean": mean(lats), "median": statistics.median(lats),
                                           "p95": pct(lats, 0.95), "total": sum(lats), "n": len(lats)}
            for kind in ("confusion", "terminology"):
                sub = [r for r in results if r["kind"] == kind and arm in r["arms"] and mode in r["arms"][arm]
                       and "contest_natural" in r["arms"][arm][mode]]
                if not sub:
                    continue
                agg = {}
                for which in ("contest_natural", "contest_controlled"):
                    cs = [r["arms"][arm][mode][which] for r in sub]
                    contested = [c for c in cs if c["contested"]]
                    agg[which] = {
                        "n": len(cs), "contested": len(contested),
                        "pass_rate_contested": mean([float(c["pass"]) for c in contested]),
                        "top1_correct": mean([float(c["top1_correct"]) for c in cs]),
                        "exact_p@5": mean([c["exact_p@5"] for c in cs]),
                        "near_miss_above": mean([float(c["near_miss_above"]) for c in cs]),
                    }
                summary["contests"].setdefault(kind, {})[key] = agg
    # paired comparisons against production, on questions where a relevant passage was retrieved
    answerable = [r for r in qs if r["arms"]["retrieval"]["passage"]["n_relevant"] > 0]
    for arm in ARMS:
        for mode in MODES:
            if arm == "current" and mode == "passage":
                continue
            pairs = [(r["arms"][arm][mode]["mrr"], r["arms"]["current"]["passage"]["mrr"])
                     for r in answerable if arm in r["arms"] and mode in r["arms"][arm]]
            if pairs:
                summary["bootstrap_vs_current"][f"{arm}|{mode}"] = paired_bootstrap([a for a, _ in pairs],
                                                                                   [b for _, b in pairs])
    strict = [r for r in answerable if r["arms"]["retrieval"]["passage"]["n_relevant"] <= STRICT_MAX_RELEVANT]
    for arm in ARMS:
        for mode in MODES:
            if arm == "current" and mode == "passage":
                continue
            pairs = [(r["arms"][arm][mode]["mrr"], r["arms"]["current"]["passage"]["mrr"])
                     for r in strict if arm in r["arms"] and mode in r["arms"][arm]]
            if pairs:
                summary.setdefault("bootstrap_strict_vs_current", {})[f"{arm}|{mode}"] = paired_bootstrap(
                    [a for a, _ in pairs], [b for _, b in pairs])
    summary["dataset"] = {
        "questions": len(qs), "answerable_questions": len(answerable), "strict_questions": len(strict),
        "confusion_tests": sum(r["kind"] == "confusion" for r in results),
        "terminology_tests": sum(r["kind"] == "terminology" for r in results),
        "verified_items": sum(r["label_basis"] == "verified" for r in results),
        "predicate_items": sum(r["label_basis"] == "predicate" for r in results),
        "uncertain_ground_truth": [r["id"] for r in results if r["ground_truth_uncertain"]],
    }
    return summary


def write_outputs(evaluated: dict[str, Any], summary: dict[str, Any], meta: dict[str, Any]) -> None:
    write_json(BENCH_DIR / "results.json", {"meta": {**meta, "models": evaluated["models"]},
                                            "summary": summary, "items": evaluated["results"]})
    with open(BENCH_DIR / "results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        head = ["id", "kind", "subject", "difficulty", "tags", "arm", "mode", "label_basis", "n_candidates",
                "n_relevant_in_pool", "rank_first"] + [f"hit@{k}" for k in KS] + \
               ["mrr", "ndcg@5", "ndcg@10", "latency_s", "failure", "contest_pass_natural",
                "contest_pass_controlled", "top1_candidate", "relevant_chunk_ids", "query"]
        w.writerow(head)
        for r in evaluated["results"]:
            for arm, modes in r["arms"].items():
                for mode, rec in modes.items():
                    w.writerow([r["id"], r["kind"], r["subject"], r["difficulty"], ";".join(r["tags"]), arm, mode,
                                r["label_basis"], r["n_candidates"], rec["n_relevant"], rec["rank_first"]]
                               + [rec[f"hit@{k}"] for k in KS]
                               + [round(rec["mrr"], 4), round(rec["ndcg@5"], 4), round(rec["ndcg@10"], 4),
                                  rec["latency_s"], rec["failure"] or "",
                                  (rec.get("contest_natural") or {}).get("pass", ""),
                                  (rec.get("contest_controlled") or {}).get("pass", ""),
                                  rec["ranking"][0] if rec["ranking"] else "",
                                  ";".join(map(str, r["relevant_in_pool"])), r["query"]])
