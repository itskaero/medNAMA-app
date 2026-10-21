"""The improvement report: what to change in MedNAMA's RAG, and why, from every stored lab run.

    python -m labs.rag_lab.improve                  # writes rag_lab/data/IMPROVEMENT_REPORT.md
    python -m labs.rag_lab.improve --since 20261008

Where scores.py-style reports rank configurations, this one looks for *causes*:

  1. where answers lose the evidence: retrieval -> reranking -> context selection -> generation -> citation
  2. what each setting did, from pairs of results that differ in exactly one setting on the same question
  3. recurring problems: junk chunks reaching the LLM, invalid citations, components no book supplied,
     the slowest stage, configurations too heavy for the 4 GB server
  4. verified production findings (known_findings.json)
  5. what to run next, where the data is missing

Every finding states its evidence, its sample size and a confidence. It is rule-based: no LLM writes
or judges anything here. Human scores are used where they exist and never invented where they don't.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from typing import Any

from . import settings, store

SERVER_RAM_BUDGET_MB = 700        # a reranker or stage adding more than this will not fit beside the backend on 4 GB
LOW_SCORE = 2                     # human accuracy/completeness at or below this is a failed answer
DIMENSIONS = ("pool_size", "top_k", "query_mode", "understanding", "coverage", "chunk_filter", "generation_mode",
              "prompt", "context_strategy", "rrf_k", "level_bonus")


def _mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return sum(xs) / len(xs) if xs else None


def _confidence(n: int, human: int = 0) -> str:
    if human >= 10:
        return "high"
    if n >= 10 or human >= 5:
        return "medium"
    return "low"


def _human(r):
    return ((r.get("human_evaluation") or {}).get("scores") or {})


def _first_rel(r):
    return ((r.get("retrieval_metrics") or {}).get("after_reranking") or {}).get("rank_first_relevant")


def _ctx_rel(r):
    return (r.get("retrieval_metrics") or {}).get("relevant_in_final_context")


def _lat(r):
    m = r.get("metrics") or {}
    return m.get("effective_total_ms", m.get("total_ms"))


def _q(r):
    return (r.get("question") or "")[:90]


def load(since: str | None) -> list[dict[str, Any]]:
    out = []
    for meta in store.list_runs():
        if since and meta["run_id"] < since:
            continue
        run = store.load_run(meta["run_id"])
        warm = bool(run["meta"].get("warm_up"))
        models = ((run["meta"].get("reproducibility") or {}).get("model_load_info") or {})
        for r in run["results"]:
            if not r.get("error"):
                r["_run"] = meta["run_id"]
                r["_level"] = meta.get("level")
                r["_warm"] = warm          # timed after model warm-up (earlier runs absorbed load times)
                r["_models"] = models      # each reranker's own load cost (RAM, time), recorded once per process
                out.append(r)
    return out


def _stage(r) -> str | None:
    """The failure stage: the human's class where scored, else recomputed with the lab's current rules
    (results stored by older lab versions keep older suggestions; they are not trusted here)."""
    from .metrics import suggest_failure

    human = (r.get("human_evaluation") or {}).get("failure_class")
    if human:
        return human
    m = (r.get("retrieval_metrics") or {}).get("after_reranking")
    return suggest_failure(m, _ctx_rel(r), (r.get("config") or {}).get("top_k") or 6) if m else None


# ── 1. where the evidence is lost ─────────────────────────────────────────────

def evidence_trail(results):
    with_gold = [r for r in results if r.get("retrieval_metrics")]
    stages: dict[str, list] = defaultdict(list)
    for r in with_gold:
        stage = _stage(r)
        if not stage and _ctx_rel(r):
            h = _human(r)
            pf = (r.get("human_evaluation") or {}).get("pass_fail") or {}
            if (h.get("accuracy") is not None and h["accuracy"] <= LOW_SCORE) or \
               (h.get("completeness") is not None and h["completeness"] <= LOW_SCORE) or \
               pf.get("answered_actual_question") == "fail":
                stage = "generation"          # the evidence reached the LLM and the answer still failed
        if stage:
            stages[stage].append(r)
    # Reranker losses another reranker avoided, on the same pool (same run, same retrieval key).
    rescued = []
    by_run = defaultdict(list)
    for r in with_gold:
        by_run[r["_run"]].append(r)
    for run, rs in by_run.items():
        for a in rs:
            if _stage(a) == "reranking":
                winners = [b for b in rs if b is not a and _ctx_rel(b) and not b["config"].get("production")
                           and b["config"].get("pool_size") == a["config"].get("pool_size")
                           and b["config"].get("query_mode") == a["config"].get("query_mode")]
                if winners:
                    rescued.append((a, winners))
    return {"with_gold": len(with_gold), "stages": stages, "rescued": rescued}


# ── 2. what each setting did ──────────────────────────────────────────────────

def _diff(a: dict, b: dict) -> list[str]:
    keys = set(DIMENSIONS) | {"stage1", "stage2"}
    return [k for k in keys if a.get(k) != b.get(k)]


def setting_effects(results):
    effects: dict[tuple, list] = defaultdict(list)
    by_run = defaultdict(list)
    for r in results:
        if not r["config"].get("production"):
            by_run[r["_run"]].append(r)
    for rs in by_run.values():
        for i, a in enumerate(rs):
            for b in rs[i + 1:]:
                d = _diff(a["config"], b["config"])
                if not d:
                    continue
                if set(d) <= {"stage1", "stage2"}:
                    dim = "reranker"
                    va = f"{a['config'].get('stage1') or 'none'}>{a['config'].get('stage2') or '-'}"
                    vb = f"{b['config'].get('stage1') or 'none'}>{b['config'].get('stage2') or '-'}"
                elif len(d) == 1:
                    dim, va, vb = d[0], a["config"].get(d[0]), b["config"].get(d[0])
                else:
                    continue                                 # differ in more than one way: not a clean pair
                lo, hi = (a, b) if str(va) <= str(vb) else (b, a)
                key = (dim, str(min(str(va), str(vb))), str(max(str(va), str(vb))))
                effects[key].append((lo, hi))
    rows = []
    for (dim, va, vb), pairs in sorted(effects.items()):
        hum = [(_human(x).get("overall"), _human(y).get("overall")) for x, y in pairs]
        hum = [(x, y) for x, y in hum if x is not None and y is not None]
        rel = [(_first_rel(x), _first_rel(y)) for x, y in pairs]
        rel = [(x, y) for x, y in rel if x is not None and y is not None]
        ctx = [(_ctx_rel(x), _ctx_rel(y)) for x, y in pairs if _ctx_rel(x) is not None and _ctx_rel(y) is not None]
        rows.append({
            "dimension": dim, "a": va, "b": vb, "pairs": len(pairs),
            "human_pairs": len(hum),
            "overall_delta": _mean([y - x for x, y in hum]),
            "first_rel_delta": _mean([y - x for x, y in rel]),
            "ctx_rel_a": _mean([float(x) for x, _ in ctx]), "ctx_rel_b": _mean([float(y) for _, y in ctx]),
            "latency_delta_ms": _mean([(_lat(y) or 0) - (_lat(x) or 0) for x, y in pairs]),
            "tokens_delta": _mean([((y["metrics"].get("tokens") or {}).get("total_tokens") or 0)
                                   - ((x["metrics"].get("tokens") or {}).get("total_tokens") or 0) for x, y in pairs]),
        })
    return rows


# ── 3. recurring problems ─────────────────────────────────────────────────────

def junk_in_context(results):
    from .retrieval import quality_flags

    hits = []
    for r in results:
        flags_by_id = {e.get("id"): e.get("flags") or [] for e in (r.get("reranking") or {}).get("ranking", [])}
        for c in r.get("context") or []:
            flags = flags_by_id.get(c.get("id")) or quality_flags(c.get("text") or "")
            flags = [f for f in flags if f != "very_short" or len((c.get("text") or "")) < 120]
            if flags:
                hits.append((r, c, flags))
    return hits


def citation_problems(results):
    out = []
    for r in results:
        cc = (r.get("answer") or {}).get("citation_check") or {}
        removed = cc.get("inline_refs_removed") or 0
        dropped = (cc.get("citations_given") or 0) - (cc.get("citations_kept") or 0)
        if removed or dropped or cc.get("supplement_had_citations"):
            out.append((r, removed, dropped, bool(cc.get("supplement_had_citations"))))
    return out


def coverage_stats(results):
    rows = [r for r in results if (r.get("coverage") or {}).get("before")]
    missing = Counter()
    gains, added, attempted, extra_ms = [], 0, 0, []
    for r in rows:
        cov = r["coverage"]
        for m in (cov.get("after") or cov["before"]).get("missing") or []:
            missing[" ".join(str(m).lower().split())[:80]] += 1
        for t in cov.get("targeted") or []:
            attempted += 1
            added += bool(t.get("added"))
        def score(c):          # recomputed from the explicit missing list (older runs stored a faulty score)
            req = c.get("required") or []
            return max(0.0, 1.0 - len(c.get("missing") or []) / len(req)) if req else None
        if cov.get("after") and score(cov["before"]) is not None and score(cov["after"]) is not None:
            gains.append(score(cov["after"]) - score(cov["before"]))
        st = (r.get("metrics") or {}).get("stages_ms") or {}
        extra_ms.append(sum(st.get(k, 0) for k in ("coverage_ms", "targeted_retrieval_ms", "coverage_after_ms")))
    return {"runs": len(rows), "missing": missing, "gain": _mean(gains), "gain_n": len(gains),
            "targeted_attempted": attempted, "targeted_added": added, "extra_ms": _mean(extra_ms)}


def _timed(results):
    warm = [r for r in results if r.get("_warm")]
    return warm or results


def stage_time(results):
    results = _timed(results)
    cur = [r for r in results if r["config"].get("production")]
    pick = cur or results
    keys = ("retrieval_ms", "rerank_ms", "generation_ms")
    med = {k: statistics.median([r["metrics"][k] for r in pick if r["metrics"].get(k) is not None] or [0]) for k in keys}
    return {"from": "CURRENT" if cur else "all configurations", "n": len(pick), **med}


def _measured_rss() -> dict[str, float]:
    """RSS growth per reranker measured after real CPU scoring in a fresh process, from the reranker
    benchmark (labs/evaluation/reranker/results.json, `run.py cpu-latency`), when it has been run."""
    path = settings.REPO / "labs" / "evaluation" / "reranker" / "results.json"
    try:
        cpu = json.loads(path.read_text(encoding="utf-8"))["meta"]["cpu_latency"]
        return {k: float(v["meta"]["rss_delta_mb"]) for k, v in cpu.items() if v.get("meta", {}).get("rss_delta_mb")}
    except Exception:
        return {}


def model_footprints(results) -> dict[str, dict[str, Any]]:
    """RAM per reranker: measured after real scoring where available, else the fp32 size of all weights.

    Neither the RSS growth at load time (weights are memory-mapped and paged in lazily: BGE v2-m3 showed
    +281 MB) nor parameters x 4 bytes is right on its own. The BGE models are multilingual and much of
    their weight is a ~250k-token embedding table of which only the rows for tokens actually seen get
    paged in, so the parameter size overstates them. RSS after real inference is the realistic figure."""
    measured = _measured_rss()
    out: dict[str, dict[str, Any]] = {}
    for r in results:
        for key, info in (r.get("_models") or {}).items():
            params = info.get("params_m")
            if not isinstance(params, (int, float)):
                continue
            label = f"{key} ({info.get('model')}, {params}M params)"
            mb = measured.get(key)
            out[label] = {"mb": mb if mb is not None else params * 4, "measured": mb is not None,
                          "weights_mb": params * 4}
    return out


def heavy_configs(results):
    return sorted(((k, v["mb"]) for k, v in model_footprints(results).items() if v["mb"] > SERVER_RAM_BUDGET_MB),
                  key=lambda x: -x[1])


# ── 4/5. assembling the report ────────────────────────────────────────────────

def build(results) -> str:
    L: list[str] = []
    n_runs = len({r["_run"] for r in results})
    n_q = len({(r.get("question_id") or r.get("question")) for r in results})
    n_h = sum(1 for r in results if _human(r))
    L += ["# MedNAMA RAG — improvement report", "",
          f"From {len(results)} answers in {n_runs} lab runs ({n_q} questions); {n_h} answers have human scores. "
          "Rule-based: every finding cites its evidence and sample size. **Confidence** is low below 10 "
          "results or 5 human scores; treat low-confidence items as leads to test, not conclusions.", ""]
    recs: list[dict[str, Any]] = []

    # verified production findings
    known = json.loads((settings.LAB_DIR / "known_findings.json").read_text(encoding="utf-8")) \
        if (settings.LAB_DIR / "known_findings.json").exists() else []
    for k in known:
        recs.append({"title": k["title"], "stage": k["stage"], "priority": {"high": 90, "medium": 60, "low": 30}[k["severity"]],
                     "confidence": "verified " + k["verified"], "evidence": k["evidence"], "change": k["suggestion"],
                     "where": k.get("where", ""), "effort": k.get("effort", "")})

    # evidence trail
    trail = evidence_trail(results)
    st = trail["stages"]
    order = ("retrieval", "reranking", "context_selection", "generation", "citation", "question_understanding")
    if any(not r.get("_warm") for r in results):
        L += [f"Note: {sum(1 for r in results if not r.get('_warm'))} answers come from runs made before model warm-up "
              "existed; their timings and RAM include model loading and are left out of the timing and RAM figures.", ""]
    L += ["## 1. Where answers lose the evidence", "",
          f"{trail['with_gold']} answers have gold evidence to trace. Failure stage per answer (human class where "
          "scored, otherwise the lab's suggestion from the evidence trail):", "",
          "| Stage | Answers | Examples |", "|---|---|---|"]
    for s in order:
        rs = st.get(s, [])
        ex = "; ".join(f"{r['label']} — “{_q(r)}”" for r in rs[:3])
        L.append(f"| {s.replace('_', ' ')} | {len(rs)} | {ex or '–'} |")
    L.append("")
    if st.get("retrieval"):
        qs = {r.get("question_id") or _q(r) for r in st["retrieval"]}
        recs.append({"title": f"Retrieval never surfaced the evidence ({len(qs)} question(s))", "stage": "retrieval",
                     "priority": 70 + 3 * len(qs), "confidence": _confidence(len(st["retrieval"])),
                     "evidence": "; ".join(sorted(map(str, qs)))[:400],
                     "change": "Check the books contain the answer (a coverage gap no reranker can fix); otherwise test "
                               "query_mode=multi or qu_rewrite and pool_size=32/50 on these questions.",
                     "where": "rewrite_query / CANDIDATE_LIMIT / corpus", "effort": "medium"})
    if trail["rescued"]:
        pairs = Counter(f"{a['label']} → rescued by {', '.join(sorted({w['label'] for w in ws}))}" for a, ws in trail["rescued"])
        recs.append({"title": f"A different reranker kept evidence that one lost ({len(trail['rescued'])} case(s))",
                     "stage": "reranking", "priority": 55 + 3 * len(trail["rescued"]),
                     "confidence": _confidence(len(trail["rescued"])),
                     "evidence": "; ".join(f"{k} ×{v}" for k, v in pairs.most_common(4)),
                     "change": "Compare these rerankers on human-scored answers before switching.", "where": "reranker choice",
                     "effort": "low"})
    if st.get("context_selection"):
        recs.append({"title": f"Relevant passage ranked in but not sent to the LLM ({len(st['context_selection'])} case(s))",
                     "stage": "context selection", "priority": 60 + 3 * len(st["context_selection"]),
                     "confidence": _confidence(len(st["context_selection"])),
                     "evidence": "; ".join(f"{r['label']} — “{_q(r)}”" for r in st["context_selection"][:3]),
                     "change": "Book diversity (select_diverse: BOOK_MARGIN, MAX_PER_BOOK) displaced it, or top-K is too "
                               "small: test context_strategy=top_k and top_k=8 on these questions.",
                     "where": "backend/app/retrieval.py select_diverse", "effort": "low"})
    if st.get("generation"):
        recs.append({"title": f"Evidence reached the LLM but the answer failed ({len(st['generation'])} case(s))",
                     "stage": "generation", "priority": 75 + 3 * len(st["generation"]),
                     "confidence": _confidence(len(st["generation"]), len(st["generation"])),
                     "evidence": "; ".join(f"{r['label']} — “{_q(r)}”" for r in st["generation"][:3]),
                     "change": "A prompt problem, not retrieval: compare prompt=experimental vs production and "
                               "generation_mode=plan/full on these questions.",
                     "where": "generation prompt", "effort": "medium"})

    # setting effects
    eff = setting_effects(results)
    L += ["## 2. What each setting changed", "",
          "Pairs of answers to the same question in the same run that differ in exactly one setting. "
          "Δ is (second value) minus (first value). Overall = human 0-5. A lower first-relevant rank is better.", "",
          "| Setting | A → B | Pairs | Δ overall (human pairs) | Δ first-relevant rank | Evidence in context A → B | Δ latency | Δ tokens |",
          "|---|---|---|---|---|---|---|---|"]
    f2 = lambda v, d=2: "–" if v is None else f"{v:+.{d}f}"  # noqa: E731
    for e in eff:
        ctx = "–" if e["ctx_rel_a"] is None else f"{e['ctx_rel_a']:.0%} → {e['ctx_rel_b']:.0%}"
        L.append(f"| {e['dimension']} | {e['a']} → {e['b']} | {e['pairs']} | {f2(e['overall_delta'])} ({e['human_pairs']}) | "
                 f"{f2(e['first_rel_delta'], 1)} | {ctx} | {f2((e['latency_delta_ms'] or 0) / 1000, 1)} s | {f2(e['tokens_delta'], 0)} |")
        if e["human_pairs"] >= 3 and e["overall_delta"] is not None and abs(e["overall_delta"]) >= 0.5:
            better = e["b"] if e["overall_delta"] > 0 else e["a"]
            recs.append({"title": f"{e['dimension']} = {better} gave better answers", "stage": e["dimension"],
                         "priority": 50 + int(20 * abs(e["overall_delta"])) + e["human_pairs"],
                         "confidence": _confidence(e["pairs"], e["human_pairs"]),
                         "evidence": f"Δ overall {e['overall_delta']:+.2f} over {e['human_pairs']} human-scored pairs; "
                                     f"latency {f2((e['latency_delta_ms'] or 0) / 1000, 1)} s",
                         "change": f"Consider {e['dimension']}={better} in production after a larger confirming run.",
                         "where": e["dimension"], "effort": "low"})
    if not eff:
        L.append("| – | no single-setting pairs yet | | | | | | |")
    L.append("")

    # recurring problems
    junk = junk_in_context(results)
    by_flag = Counter(f for _, _, fl in junk for f in fl)
    L += ["## 3. Recurring problems", "", "### Low-quality chunks that reached the LLM", ""]
    if junk:
        L += ["A passage counts when the chunk retrieval matched is a heading, a fragment, an index entry or a "
              "bibliography line (the text shown may be longer: production expands it with neighbouring paragraphs).", "",
              f"{len(junk)} context passages carried a quality flag: " + ", ".join(f"{k} ×{v}" for k, v in by_flag.most_common()) + ".", ""]
        for r, c, fl in junk[:6]:
            L.append(f"- **{r['label']}**, “{_q(r)}”: {c.get('book')} p.{c.get('page')} [{', '.join(fl)}] — "
                     f"“{' '.join((c.get('text') or '').split())[:110]}”")
        L.append("")
        recs.append({"title": f"Heading-only / bibliography / index chunks reach the LLM ({len(junk)} passages)",
                     "stage": "chunk quality", "priority": 45 + min(30, 2 * len(junk)), "confidence": _confidence(len(junk)),
                     "evidence": ", ".join(f"{k} ×{v}" for k, v in by_flag.most_common()),
                     "change": "Test chunk_filter=true (sweep it) and, if answers hold up, demote these chunks before "
                               "context selection in production.", "where": "retrieval candidate filtering", "effort": "low"})
    else:
        L += ["None found in the stored runs.", ""]

    cites = citation_problems(results)
    L += ["### Citations", ""]
    if cites:
        tot_removed = sum(x[1] for x in cites)
        L += [f"{len(cites)} answers had citations the validator had to strip ({tot_removed} invalid inline references).", ""]
        for r, removed, dropped, supp in cites[:5]:
            L.append(f"- {r['label']}, “{_q(r)}”: {removed} inline removed, {dropped} listed citations dropped"
                     + (", citation inside the AI supplement" if supp else ""))
        L.append("")
        recs.append({"title": f"The model cites pages it was not given ({len(cites)} answers)", "stage": "citation",
                     "priority": 50 + 3 * len(cites), "confidence": _confidence(len(cites)),
                     "evidence": f"{tot_removed} invalid inline references removed by validate_generation",
                     "change": "Validation already strips them; if frequent, tighten the citation instruction or "
                               "number the passages and cite by number.", "where": "generation prompt", "effort": "low"})
    else:
        L += ["No invalid citations were stripped in the experimental runs (production answers report none).", ""]

    cov = coverage_stats(results)
    L += ["### Evidence coverage", ""]
    if cov["runs"]:
        L += [f"{cov['runs']} answers ran a coverage check (extra time {((cov['extra_ms'] or 0) / 1000):.1f} s each on average). "
              f"Targeted retrieval added a passage for {cov['targeted_added']} of {cov['targeted_attempted']} missing "
              f"components; coverage gain {f2(cov['gain'])} over {cov['gain_n']} answers.", "",
              "Components the passages most often still lacked (candidate corpus or query gaps):", ""]
        L += [f"- {k} ×{v}" for k, v in cov["missing"].most_common(8)] + [""]
    else:
        L += ["No run used the coverage check yet.", ""]

    tm = stage_time(results)
    biggest = max(("retrieval_ms", "rerank_ms", "generation_ms"), key=lambda k: tm[k])
    L += ["### Where the time goes", "",
          f"Median per answer ({tm['from']}, n={tm['n']}): retrieval {tm['retrieval_ms'] / 1000:.1f} s, reranking "
          f"{tm['rerank_ms'] / 1000:.1f} s, generation {tm['generation_ms'] / 1000:.1f} s. Largest: **{biggest.replace('_ms', '')}**.", ""]
    if tm["n"] and tm[biggest] > 5000:
        recs.append({"title": f"{biggest.replace('_ms', '').capitalize()} dominates latency ({tm[biggest] / 1000:.1f} s median)",
                     "stage": "latency", "priority": 40, "confidence": _confidence(tm["n"]),
                     "evidence": f"median over {tm['n']} {tm['from']} answers",
                     "change": {"retrieval_ms": "Retrieval: the CPU embedding and the per-book probes (level set) dominate; "
                                                 "consider caching rewrites/embeddings or fewer probes.",
                                "rerank_ms": "Reranking: smaller pool or dropping the second stage.",
                                "generation_ms": "Generation: shorter context (top-K) or max_tokens."}[biggest],
                     "where": biggest.replace("_ms", ""), "effort": "medium"})

    feet = model_footprints(results)
    if feet:
        L += ["### Model memory and the 4 GB server", "",
              "RAM per reranker on CPU. \"Measured\" = process growth after real scoring in a fresh process "
              "(labs/evaluation/reranker `cpu-latency`); otherwise the fp32 size of all weights, which overstates the "
              "multilingual BGE models. The server has ~1.3 GB free beside the backend:", ""]
        L += [f"- {k}: {v['mb']:.0f} MB {'measured' if v['measured'] else 'from weights'} "
              f"(weights {v['weights_mb']:.0f} MB)"
              + ("  ← **over the " + str(SERVER_RAM_BUDGET_MB) + " MB budget**" if v["mb"] > SERVER_RAM_BUDGET_MB else "")
              for k, v in sorted(feet.items(), key=lambda x: -x[1]["mb"])]
        L += ["", "_The first model loaded in a process also carries the PyTorch runtime (a few hundred MB): that is "
                  "why ms-marco's measured figure, loaded first, exceeds its weights._", ""]
    heavy = heavy_configs(results)
    if heavy:
        L += ["Models over budget can be studied here but not deployed on the current server.", ""]
    excluded = len(results) - len(_timed(results))
    if excluded:
        L += [f"_{excluded} answers from runs before the warm-up step are excluded from timing (they absorbed model loading)._", ""]

    # recommendations
    recs.sort(key=lambda x: -x["priority"])
    L += ["## 4. Recommendations (highest priority first)", ""]
    for i, r in enumerate(recs, 1):
        L += [f"**{i}. {r['title']}**  ", f"Stage: {r['stage']} · Confidence: {r['confidence']} · Effort: {r['effort']}  ",
              f"Evidence: {r['evidence']}  ", f"Change: {r['change']}" + (f"  \nWhere: `{r['where']}`" if r.get("where") else ""), ""]

    # what to run next
    tested = {e["dimension"] for e in eff}
    todo = []
    if n_h < 10:
        todo.append(f"Score answers in the UI: only {n_h} human-scored answers. Answer-quality findings need 10+ per configuration.")
    for dim, cmd in (("pool_size", "--sweep pool_size=16,32,50"), ("top_k", "--sweep top_k=5,6,8"),
                     ("generation_mode", "--sweep generation_mode=direct,plan,full"),
                     ("coverage", "--sweep coverage=none,check,check_retrieve"), ("chunk_filter", "--sweep chunk_filter=false,true"),
                     ("query_mode", "--sweep query_mode=original,rewrite,qu_rewrite,multi"),
                     ("prompt", "--sweep prompt=experimental,production")):
        if dim not in tested:
            todo.append(f"No data on **{dim}** yet: `python -m labs.rag_lab.benchmark --configs msmarco_medcpt {cmd}`")
    if n_q < 10:
        todo.append(f"Only {n_q} question(s) tested: run all benchmark questions (`python -m labs.rag_lab.benchmark`).")
    L += ["## 5. What to run next", ""] + [f"- {t}" for t in todo] + [""] if todo else []
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--out", default=str(settings.DATA / "IMPROVEMENT_REPORT.md"))
    args = ap.parse_args()
    text = build(load(args.since))
    settings.DATA.mkdir(parents=True, exist_ok=True)
    open(args.out, "w", encoding="utf-8").write(text)
    print(text)
    print(f"\n(saved to {args.out})")


if __name__ == "__main__":
    main()
