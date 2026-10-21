"""RERANKER_BENCHMARK.md from results: every table is generated; the interpretation is not.

The prose that needs judgement (executive summary, why an example failed, the recommendation) lives
in analysis_notes.md, written after reading the results and spliced in here, so regenerating the
tables never deletes it. Sections are matched by their "## " heading.
"""

from __future__ import annotations

import math
import re
import statistics
from typing import Any

from .common import BENCH_DIR
from .evaluate import ARMS, HEADLINE, KS, pct
from .rerankers import MODELS

NOTES = BENCH_DIR / "analysis_notes.md"
OUT = BENCH_DIR / "RERANKER_BENCHMARK.md"
LABEL = {
    "retrieval": "Retrieval only (RRF)", "current": "**Current** (ms-marco → MedCPT top 8)",
    "msmarco": "ms-marco-MiniLM-L-6 alone", "medcpt": "MedCPT alone", "bge_v2_m3": "BGE v2-m3",
    "bge_base": "BGE base", "msmarco>bge_base@8": "ms-marco → BGE base top 8",
    "msmarco>bge_v2_m3@8": "ms-marco → BGE v2-m3 top 8", "medcpt+bge_v2_m3": "MedCPT + BGE v2-m3 (RRF fusion)",
    "current@prod_pool": "Current, production's ~16-candidate pool",
    "msmarco>bge_base@8@prod_pool": "ms-marco → BGE base top 8, production pool",
    "bge_v2_m3@prod_pool": "BGE v2-m3, production pool",
}
SUBJECT_ORDER = ["anatomy", "physiology", "biochemistry", "pathology", "pharmacology", "microbiology", "paediatrics",
                 "medicine", "surgery", "ent", "neurology", "haematology", "genetics", "emergency_medicine"]


def f(x: Any, digits: int = 3) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x:.{digits}f}" if isinstance(x, float) else str(x)


def ms(x: Any) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 1000:.0f} ms"


def table(head: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def notes() -> dict[str, str]:
    if not NOTES.exists():
        return {}
    text = NOTES.read_text(encoding="utf-8")
    parts = re.split(r"^## ", text, flags=re.M)
    return {p.split("\n", 1)[0].strip(): p.split("\n", 1)[1].strip() for p in parts[1:] if "\n" in p}


def note(n: dict[str, str], heading: str, fallback: str = "") -> str:
    return n.get(heading) or fallback or "_Not written yet: add a `## " + heading + "` section to analysis_notes.md._"


def write_report(evaluated: dict[str, Any], summary: dict[str, Any], meta: dict[str, Any]) -> None:
    n = notes()
    ds = summary["dataset"]
    res = evaluated["results"]
    arms_present = [a for a in ARMS if f"{a}|passage" in summary["overall"]]
    head_arms = [a for a in HEADLINE if a in arms_present]
    lines: list[str] = ["# MedNAMA reranker benchmark", ""]
    provisional = ds["verified_items"] == 0
    lines += [
        f"Generated {meta['generated_at']} by `labs/evaluation/reranker/run.py evaluate`. Tables are generated; the prose "
        "comes from `analysis_notes.md`.", "",
        ("> **Status: provisional ground truth.** All {p} items are labelled by their gold predicates (the terms the "
         "answer passage must contain), checked against the corpus; none has human-verified chunk ids yet. Rankings "
         "between models are meaningful; absolute numbers will move once `run.py sheet` verification is done."
         .format(p=ds["predicate_items"])) if provisional else
        f"> Ground truth: {ds['verified_items']} items verified by chunk id, {ds['predicate_items']} by predicate.",
        "",
        "## Executive summary", "", note(n, "Executive summary"), "",
    ]

    # ── setup ──
    rows = []
    for key, name in MODELS.items():
        m = evaluated["models"].get(f"{key}/passage", {})
        rows.append([f"`{name}`", f(m.get("revision", "–"))[:12] if m.get("revision") else "–",
                     f"{m.get('params_m', '–')}M", f(m.get("gpu_peak_mb"), 0) + " MB" if m.get("gpu_peak_mb") else "–"])
    cm = meta.get("candidates", {})
    lines += ["## Setup", "",
              f"* Corpus: the production dev database (25 ready books). Pool: top {cm.get('pool_size', 50)} parents by "
              f"production's RRF over vector + keyword lists of {cm.get('candidate_limit')} rows for the question and its "
              f"recorded LLM rewrite, plus each list's top {cm.get('per_list_top')} (production reranks the top "
              f"{cm.get('production_rerank_pool')} plus those heads).",
              f"* Items: {ds['questions']} questions ({ds['answerable_questions']} with a relevant passage in the pool), "
              f"{ds['confusion_tests']} confusion tests, {ds['terminology_tests']} terminology tests.",
              f"* Every model scores each candidate against the question and the rewrite and keeps the max, as production does. "
              f"fp32, max_length 512, batch 32, eval mode, seed 0.",
              f"* Hardware: {meta.get('gpu')} (CUDA {meta.get('cuda')}); CPU timings on this PC's CPU. "
              f"Python {meta['python']}, torch {meta['torch']}, sentence-transformers {meta['sentence_transformers']}, "
              f"transformers {meta['transformers']}.", "",
              table(["Model", "Revision", "Params", "GPU peak"], rows), ""]

    # ── overall ──
    def metric_rows(scope: str) -> list[list[str]]:
        out = []
        for a in arms_present:
            s = summary[scope].get(f"{a}|passage")
            lat = summary["latency"].get(f"{a}|passage", {})
            if not s or s.get("n", 0) == 0:
                continue
            out.append([LABEL[a]] + [f(s[f"hit@{k}"]) for k in (1, 5, 10, 20, 50)] +
                       [f(s["mrr"]), f(s["ndcg@5"]), f(s["ndcg@10"]), ms(lat.get("mean"))])
        return out

    head = ["Arm", "Hit@1", "Hit@5", "Hit@10", "Hit@20", "Hit@50", "MRR", "nDCG@5", "nDCG@10", "GPU latency"]
    boot_rows = []
    for a in arms_present:
        b = summary["bootstrap_vs_current"].get(f"{a}|passage")
        if b and a != "current":
            sig = "yes" if b["lo"] > 0 or b["hi"] < 0 else "no"
            boot_rows.append([LABEL[a], f"{b['diff']:+.3f}", f"[{b['lo']:+.3f}, {b['hi']:+.3f}]", sig,
                              f"{b['wins']} / {b['losses']} / {b['ties']}"])
    lines += ["## Overall metrics", "",
              "Passage-only input (what production scores). **Hit@k** = a relevant passage is in the top k (what "
              "single-answer QA usually calls Recall@k); Recall@k over all relevant passages, per question, is in "
              "`results.json`. Hit@50 is the same for every reranker: it is whether retrieval found the passage at all.",
              "", f"### All {ds['questions']} questions", "", table(head, metric_rows("overall")), "",
              f"### The {ds['answerable_questions']} questions whose answer was retrieved", "",
              "Only these can separate rerankers: on the others every reranker scores 0.", "",
              table(head, metric_rows("answerable")), "",
              f"### The {ds.get('strict_questions', 0)} strict questions (answerable, at most 5 relevant passages in the pool)", "",
              "Where many passages satisfy a predicate (\"lateral rectus\" + \"abducens\" matches 20), any model "
              "finds *a* relevant one. Here a reranker has to find the needle.", "",
              table(head, metric_rows("strict")), "",
              "### Is the difference from production real?", "",
              "Paired bootstrap over the answerable questions (2,000 resamples): MRR of the arm minus MRR of "
              "**current**, with a 95% interval. Wins / losses / ties count questions.", "",
              table(["Arm", "ΔMRR vs current", "95% interval", "Excludes 0?", "W / L / T"], boot_rows), "",
              "Strict subset only:", "",
              table(["Arm", "ΔMRR vs current", "95% interval", "Excludes 0?", "W / L / T"], _boot(summary, "bootstrap_strict_vs_current", arms_present)), "",
              note(n, "Overall metrics", " "), ""]

    # ── subjects ──
    rows = []
    for subj in [s for s in SUBJECT_ORDER if s in summary["by_subject"]]:
        cells = [subj.replace("_", " "), str(summary["by_subject"][subj].get("current|passage", {}).get("n", ""))]
        for a in head_arms[1:]:
            s = summary["by_subject"][subj].get(f"{a}|passage", {})
            cells.append(f"{f(s.get('mrr'), 2)} / {f(s.get('hit@5'), 2)}")
        rows.append(cells)
    lines += ["## Subject performance", "", "MRR / Hit@5 per subject, passage input. Four to six questions per "
              "subject: read these as signals, not verdicts; one question moves a cell by 0.2.", "",
              table(["Subject", "n"] + [LABEL[a].replace("**", "") for a in head_arms[1:]], rows), "",
              note(n, "Subject performance", " "), ""]
    for bucket, title in (("by_difficulty", "difficulty"), ("by_tag", "question type")):
        rows = []
        for val, d in summary[bucket].items():
            cells = [val.replace("_", " "), str(d.get("current|passage", {}).get("n", ""))]
            cells += [f(d.get(f"{a}|passage", {}).get("mrr"), 2) for a in head_arms[1:]]
            rows.append(cells)
        lines += [f"### MRR by {title}", "", table([title.capitalize(), "n"] + [LABEL[a].replace("**", "") for a in head_arms[1:]], rows), ""]

    # ── contests ──
    for kind, title, extra in (("confusion", "Medical confusion performance", "pass"),
                               ("terminology", "Exact terminology performance", "exact")):
        agg = summary["contests"].get(kind, {})
        rows = []
        for a in arms_present:
            g = agg.get(f"{a}|passage")
            if not g:
                continue
            nat, ctl = g["contest_natural"], g["contest_controlled"]
            if kind == "confusion":
                rows.append([LABEL[a], f"{f(ctl['pass_rate_contested'], 2)} ({ctl['contested']})",
                             f"{f(nat['pass_rate_contested'], 2)} ({nat['contested']})", f(ctl["top1_correct"], 2)])
            else:
                rows.append([LABEL[a], f(ctl["top1_correct"], 2), f(ctl["exact_p@5"], 2),
                             f(ctl["near_miss_above"], 2), f(nat["top1_correct"], 2)])
        if kind == "confusion":
            head = ["Arm", "Correct above every distractor: controlled (n contested)", "…natural pool (n)", "Top-1 correct (controlled)"]
            intro = ("Each test pits the correct concept against named competitors (`medical_confusion_tests.json`). "
                     "**Controlled** adds the nearest real textbook passage for any concept retrieval did not surface, "
                     "so every contest actually happens; **natural** uses the retrieval pool as it came.")
        else:
            head = ["Arm", "Top-1 has the exact term", "Exact-term share of top 5", "Near-miss ranked above exact", "Top-1 exact (natural)"]
            intro = ("Bare-term queries (`terminology_tests.json`): does the top of the ranking contain the exact term, "
                     "or a conceptually similar passage without it? Controlled pools as above.")
        grid = []
        for r in [r for r in res if r["kind"] == kind]:
            cells = [r["id"], r["query"][:48]]
            for a in head_arms[1:]:
                c = r["arms"].get(a, {}).get("passage", {}).get("contest_controlled")
                if not c:
                    cells.append("–")
                elif kind == "confusion":
                    cells.append("–" if not c["contested"] else ("✓" if c["pass"] else f"✗ ({c['correct_rank']})"))
                else:
                    cells.append(f"{'✓' if c['top1_correct'] else '✗'} {c['exact_p@5']:.1f}")
            grid.append(cells)
        lines += [f"## {title}", "", intro, "", table(head, rows), "",
                  "Per test (controlled pool; " + ("✗ (n) = correct concept's best rank n" if kind == "confusion"
                                                   else "✓/✗ = top-1 exact, then exact share of top 5") + "):", "",
                  table(["Test", "Query"] + [LABEL[a].replace("**", "") for a in head_arms[1:]], grid), "",
                  note(n, title, " "), ""]

    # ── structured metadata ──
    rows = []
    for a in [x for x in arms_present if x != "retrieval"]:
        p, s = summary["answerable"].get(f"{a}|passage"), summary["answerable"].get(f"{a}|structured")
        if not p or not s:
            continue
        b = summary["bootstrap_vs_current"].get(f"{a}|structured")
        cp = summary["contests"].get("confusion", {}).get(f"{a}|passage", {}).get("contest_controlled", {})
        cs = summary["contests"].get("confusion", {}).get(f"{a}|structured", {}).get("contest_controlled", {})
        rows.append([LABEL[a], f(p["mrr"]), f(s["mrr"]), f"{s['mrr'] - p['mrr']:+.3f}", f(p["hit@1"]), f(s["hit@1"]),
                     f(cp.get("pass_rate_contested"), 2), f(cs.get("pass_rate_contested"), 2)])
    lines += ["## Structured metadata experiment", "",
              "The same candidates, scored as passage only versus `[BOOK] / [CHAPTER] / [SECTION] / [PAGE] / [PASSAGE]` "
              "built from the metadata the corpus really has (chapter path split on ` > `; nothing invented). "
              "Answerable questions; confusion = controlled pass rate.", "",
              table(["Arm", "MRR passage", "MRR structured", "Δ", "Hit@1 passage", "Hit@1 structured",
                     "Confusion passage", "Confusion structured"], rows), "",
              note(n, "Structured metadata experiment", " "), ""]

    # ── failures ──
    classes = ["retriever", "reranker", "metadata", "chunking", "ground_truth"]
    rows = []
    for a in arms_present:
        fails = summary["failures"].get(f"{a}|passage", {})
        rows.append([LABEL[a]] + [str(fails.get(c, 0)) for c in classes] + [str(sum(fails.values()))])
    lines += ["## Failure analysis", "",
              f"A miss = no relevant passage in the top {5} (production gives the LLM 5-6 blocks). Questions and "
              "confusion tests, natural pool. **Retriever**: never in the top 50. **Reranker**: retrieved, ranked "
              "below 5. **Metadata**: the relevant passage lacks chapter or page. **Chunking**: the answer is split "
              "across neighbouring chunks, or lies outside the 1,500-character window the reranker reads. "
              "**Ground truth**: the predicate matches nothing in the corpus, or verified ids are gone.", "",
              table(["Arm"] + [c.replace("_", " ").capitalize() for c in classes] + ["Total"], rows), "",
              _examples(res, head_arms), "",
              note(n, "Failure analysis", " "), ""]

    # ── performance ──
    rows = []
    for a in arms_present:
        lat = summary["latency"].get(f"{a}|passage")
        if lat:
            rows.append([LABEL[a], ms(lat["mean"]), ms(lat["median"]), ms(lat["p95"]), f"{lat['total']:.1f} s"])
    cpu_rows = []
    cpu = meta.get("cpu_latency", {})
    heads = meta.get("cpu_heads", {})
    for key, d in cpu.items():
        secs = d["seconds"]
        cpu_rows.append([f"`{MODELS[key]}` (full pool)", ms(statistics.mean(secs)), ms(statistics.median(secs)),
                         ms(pct(secs, 0.95)), f"{d['meta'].get('rss_delta_mb')} MB", str(d["meta"].get("torch_threads"))])
    if "msmarco" in cpu:
        for second in ("medcpt", "bge_base", "bge_v2_m3"):
            h = heads.get(second) or {}
            ids = list(h)
            if not ids:
                continue
            s1 = dict(zip(ids, cpu["msmarco"]["seconds"]))
            total = [s1.get(i, 0) + h[i] for i in ids]
            cpu_rows.append([f"ms-marco → {second} top 8 (production shape)", ms(statistics.mean(total)),
                             ms(statistics.median(total)), ms(pct(total, 0.95)), "–", "–"])
    lines += ["## Performance", "", "### GPU (RTX 2060), every item, passage input", "",
              table(["Arm", "Mean", "Median", "p95", "Total"], rows), "",
              "### CPU (this PC, a subset of items): the closer guide to the NAS", "",
              table(["Model", "Mean", "Median", "p95", "RSS increase", "Threads"], cpu_rows) if cpu_rows else
              "_Not run: `run.py cpu-latency`._", "",
              note(n, "Performance", " "), ""]

    lines += ["## Recommendation", "", note(n, "Recommendation", "See RECOMMENDATION.md."), "",
              "## Reproducing", "", "See `README.md`. Per-question rankings, scores and failure classes: "
              "`results.json` and `results.csv`.", ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")


def _boot(summary: dict[str, Any], key: str, arms: list[str], mode: str = "passage") -> list[list[str]]:
    rows = []
    for a in arms:
        b = summary.get(key, {}).get(f"{a}|{mode}")
        if b and a != "current":
            sig = "yes" if b["lo"] > 0 or b["hi"] < 0 else "no"
            rows.append([LABEL[a], f"{b['diff']:+.3f}", f"[{b['lo']:+.3f}, {b['hi']:+.3f}]", sig,
                         f"{b['wins']} / {b['losses']} / {b['ties']}"])
    return rows


def _examples(res: list[dict[str, Any]], arms: list[str]) -> str:
    """Questions where one model clearly beats the others, and where everything fails."""
    def rank(r, a):
        x = r["arms"].get(a, {}).get("passage", {})
        return x.get("rank_first")

    def fmt(r):
        cells = ", ".join(f"{a} {rank(r, a) or '—'}" for a in ("current", "medcpt", "bge_v2_m3", "bge_base") if a in arms)
        return f"* **{r['id']}** {r['query'][:110]} — rank of first relevant: {cells}"

    qs = [r for r in res if r["kind"] in ("question", "confusion") and r["arms"]["retrieval"]["passage"]["n_relevant"] > 0]
    big = 10 ** 6

    def wins(a, others):
        return [r for r in qs if (rank(r, a) or big) <= 3 and all((rank(r, o) or big) > (rank(r, a) or big) + 2 for o in others)]

    out = []
    for a, others, title in (("medcpt", ["bge_v2_m3", "bge_base", "msmarco"], "MedCPT clearly ahead"),
                             ("bge_v2_m3", ["medcpt", "current"], "BGE v2-m3 clearly ahead"),
                             ("current", ["medcpt", "bge_v2_m3"], "Current (two-stage) clearly ahead")):
        if a not in arms:
            continue
        found = wins(a, others)
        out += [f"**{title}** ({len(found)}):", ""] + [fmt(r) for r in found[:8]] + [""]
    allfail = [r for r in res if r["kind"] in ("question", "confusion")
               and all((rank(r, a) or big) > 5 for a in arms if a != "retrieval")]
    out += [f"**Every reranker misses the top 5** ({len(allfail)}):", ""]
    for r in allfail[:12]:
        why = r["arms"]["current"]["passage"].get("failure")
        out.append(fmt(r) + f" — class: {why}")
    return "\n".join(out)
