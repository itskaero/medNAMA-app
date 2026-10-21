"""The MedNAMA RAG Lab report, aggregated from every stored run and evaluation.

    python -m labs.rag_lab.report                 # all runs
    python -m labs.rag_lab.report --since 20261008  # runs whose id starts at or after this

Human scores are the answer-quality measure. AI-judge scores, when present, are reported in their own
columns and labelled. Retrieval metrics (MRR) only exist for questions with gold evidence.

Significance: differences against CURRENT are tested only when at least MIN_PAIRED questions have human
"overall" scores for both configurations (paired bootstrap, 95% interval, plus win/loss counts).
Below that the report says so instead of naming a winner.
"""

from __future__ import annotations

import argparse
import random
import statistics
from collections import defaultdict
from typing import Any

from . import settings, store

MIN_PAIRED = 10
MIN_FOR_BEST = 3          # a "best" label needs at least this many scored answers behind it
HUMAN_KEYS = ("accuracy", "completeness", "relevance", "grounding", "citation_correctness", "reasoning_quality",
              "exam_level_appropriateness", "overall")
PASS_KEYS = ("factual_correctness", "answered_actual_question", "major_omissions", "unsupported_claims",
             "citation_errors", "hallucination")


def _mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return sum(xs) / len(xs) if xs else None


def _base(name: str) -> str:
    return name


def collect(since: str | None = None) -> list[dict[str, Any]]:
    rows = []
    for r in store.list_runs():
        if since and r["run_id"] < since:
            continue
        run = store.load_run(r["run_id"])
        for res in run["results"]:
            if res.get("error"):
                continue
            rows.append({"run_id": r["run_id"], "question_id": r.get("question_id") or r["question"][:40],
                         "level": r["level"], "res": res})
    return rows


def _bootstrap(diffs: list[float], n: int = 2000, seed: int = 0) -> tuple[float, float, float]:
    rng = random.Random(seed)
    boots = sorted(statistics.mean(rng.choice(diffs) for _ in diffs) for _ in range(n))
    return statistics.mean(diffs), boots[int(0.025 * n)], boots[int(0.975 * n) - 1]


def build(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_cfg: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_cfg[row["res"]["configuration"]].append(row)
    table = {}
    for cfg, items in by_cfg.items():
        res = [i["res"] for i in items]
        human = [r.get("human_evaluation") or {} for r in res]
        ai = [(r.get("ai_evaluation") or {}).get("scores") or {} for r in res]
        mrrs = [((r.get("retrieval_metrics") or {}).get("after_reranking") or {}).get("mrr") for r in res]
        lat = [r["metrics"].get("effective_total_ms", r["metrics"]["total_ms"]) for r in res if r.get("metrics")]
        table[cfg] = {
            "label": res[0].get("label", cfg), "answers": len(res),
            "human_scored": sum(1 for h in human if h.get("scores")),
            "mrr": _mean(mrrs), "mrr_n": sum(1 for m in mrrs if m is not None),
            **{f"human_{k}": _mean([(h.get("scores") or {}).get(k) for h in human]) for k in HUMAN_KEYS},
            **{f"pass_{k}": _mean([1.0 if (h.get("pass_fail") or {}).get(k) == "pass" else 0.0
                                   for h in human if (h.get("pass_fail") or {}).get(k) in ("pass", "fail")])
               for k in PASS_KEYS},
            **{f"ai_{k}": _mean([a.get(k) for a in ai]) for k in ("accuracy", "completeness", "overall")},
            "latency_mean_ms": _mean(lat), "latency_median_ms": statistics.median(lat) if lat else None,
            "rerank_ms_mean": _mean([r["metrics"].get("rerank_ms") for r in res]),
            "rss_peak_mb_mean": _mean([r["metrics"].get("rss_peak_mb") for r in res]),
            "rss_increase_mb_mean": _mean([r["metrics"].get("rss_increase_mb") for r in res]),
            "cpu_time_s_mean": _mean([r["metrics"].get("cpu_time_s") for r in res]),
            "tokens_mean": _mean([((r["metrics"].get("tokens") or {}).get("total_tokens")) for r in res]),
            "failure_classes": _count([h.get("failure_class") for h in human if h.get("failure_class")]),
        }
    # paired comparisons against CURRENT on human overall scores
    comparisons = {}
    cur = {(i["question_id"], i["level"]): (i["res"].get("human_evaluation") or {}).get("scores", {}).get("overall")
           for i in by_cfg.get("current", [])}
    for cfg, items in by_cfg.items():
        if cfg == "current":
            continue
        diffs = []
        for i in items:
            other = (i["res"].get("human_evaluation") or {}).get("scores", {}).get("overall")
            base = cur.get((i["question_id"], i["level"]))
            if isinstance(other, (int, float)) and isinstance(base, (int, float)):
                diffs.append(other - base)
        entry: dict[str, Any] = {"paired": len(diffs), "wins": sum(d > 0 for d in diffs),
                                 "losses": sum(d < 0 for d in diffs), "ties": sum(d == 0 for d in diffs)}
        if len(diffs) >= MIN_PAIRED:
            m, lo, hi = _bootstrap(diffs)
            entry.update({"mean_diff": m, "ci95": [lo, hi], "significant": lo > 0 or hi < 0})
        else:
            entry["note"] = f"only {len(diffs)} paired human scores (< {MIN_PAIRED}): no significance claimed"
        comparisons[cfg] = entry
    return {"table": table, "comparisons": comparisons, "rows": len(rows)}


def _count(xs):
    out: dict[str, int] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


def _best(table: dict, key: str, lowest: bool = False, min_n_key: str | None = None) -> str:
    cands = [(v[key], k) for k, v in table.items()
             if v.get(key) is not None and (not min_n_key or (v.get(min_n_key) or 0) >= MIN_FOR_BEST)]
    if not cands:
        return f"not enough data (needs {MIN_FOR_BEST}+ scored answers per configuration)" if min_n_key else "not enough data"
    val, name = (min if lowest else max)(cands)
    shown = f"{val / 1000:.1f} s" if key.endswith("_ms") else (f"{val:.0f} MB" if key.endswith("_mb") or "_mb_" in key else f"{val:.2f}")
    return f"{table[name]['label']} ({shown})"


def render(rep: dict[str, Any]) -> str:
    t = rep["table"]
    f = lambda v, d=2: "–" if v is None else (f"{v:.{d}f}" if isinstance(v, float) else str(v))  # noqa: E731
    lines = ["=" * 78, "MEDNAMA RAG LAB REPORT", "=" * 78, "",
             f"{rep['rows']} answers from stored runs. Human scores are answer quality; 'AI' columns are an AI judge "
             f"(not ground truth). MRR only for questions with gold evidence.", "",
             f"{'Configuration':60}{'MRR':>7}{'Accur.':>8}{'Compl.':>8}{'Overall':>8}{'Latency':>10}{'RAM+':>8}{'n/h':>7}",
             "-" * 116]
    for k, v in sorted(t.items(), key=lambda kv: kv[0] != "current"):
        lines.append(f"{v['label'][:59]:60}{f(v['mrr']):>7}{f(v['human_accuracy']):>8}{f(v['human_completeness']):>8}"
                     f"{f(v['human_overall']):>8}{f((v['latency_mean_ms'] or 0) / 1000, 1) + 's':>10}"
                     f"{f(v['rss_increase_mb_mean'], 0):>8}{str(v['answers']) + '/' + str(v['human_scored']):>7}")
    lines += ["", "n/h = answers / human-scored answers. RAM+ = peak RSS increase during the run (MB); models loaded",
              "by an earlier configuration in the same process are not counted again.", ""]
    ai = [(k, v) for k, v in t.items() if v.get("ai_overall") is not None]
    if ai:
        lines += ["AI evaluation — not ground truth:"] + [
            f"  {v['label'][:40]:42} accuracy {f(v['ai_accuracy'])}  completeness {f(v['ai_completeness'])}  overall {f(v['ai_overall'])}"
            for k, v in ai] + [""]
    lines += [
        f"BEST RETRIEVAL        {_best(t, 'mrr', min_n_key='mrr_n')}",
        f"BEST ANSWER QUALITY   {_best(t, 'human_accuracy', min_n_key='human_scored')}",
        f"BEST COMPLETENESS     {_best(t, 'human_completeness', min_n_key='human_scored')}",
        f"FASTEST               {_best(t, 'latency_mean_ms', lowest=True)}",
        f"LOWEST RAM            {_best(t, 'rss_increase_mb_mean', lowest=True)}",
        f"BEST OVERALL          {_best(t, 'human_overall', min_n_key='human_scored')}",
        "", "Against CURRENT (human 'overall', paired by question and level):"]
    for k, c in rep["comparisons"].items():
        if "mean_diff" in c:
            lines.append(f"  {t[k]['label'][:40]:42} {c['mean_diff']:+.2f} [{c['ci95'][0]:+.2f}, {c['ci95'][1]:+.2f}] "
                         f"W/L/T {c['wins']}/{c['losses']}/{c['ties']}{'  significant' if c['significant'] else ''}")
        else:
            lines.append(f"  {t[k]['label'][:40]:42} {c['note']}")
    fails = {k: v["failure_classes"] for k, v in t.items() if v["failure_classes"]}
    if fails:
        lines += ["", "Human failure classifications:"] + [f"  {t[k]['label'][:40]:42} {v}" for k, v in fails.items()]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--out", default=str(settings.DATA / "REPORT.txt"))
    args = ap.parse_args()
    rep = build(collect(args.since))
    text = render(rep)
    print(text)
    settings.DATA.mkdir(parents=True, exist_ok=True)
    open(args.out, "w", encoding="utf-8").write(text)
    print(f"\n(saved to {args.out})")


if __name__ == "__main__":
    main()
