"""Run the benchmark questions through configurations, then print the report.

    python -m labs.rag_lab.benchmark                                   # all questions, default six configurations
    python -m labs.rag_lab.benchmark --questions lab001 lab003 --level FCPS-II --mode tutor
    python -m labs.rag_lab.benchmark --configs msmarco_medcpt msmarco_bgebase --sweep pool_size=16,32,50 top_k=5,6,8
    python -m labs.rag_lab.benchmark --configs msmarco_medcpt --sweep generation_mode=direct,plan,full
    python -m labs.rag_lab.benchmark --ai-judge                        # also store an AI judge score (labelled)
    python -m labs.rag_lab.benchmark --list                            # configurations and questions

Each question's own "level" is used unless --level is given. Every run is stored under
rag_lab/data/runs and can be opened and human-scored in the UI (python -m labs.rag_lab.server).
"""

from __future__ import annotations

import argparse
import time

from . import settings
from .configs import DEFAULT_SET, OVERRIDABLE, PRESETS, resolve, sweep


def _parse_sweep(items: list[str]) -> dict[str, list[str]]:
    dims = {}
    for it in items or []:
        key, _, vals = it.partition("=")
        if key not in OVERRIDABLE:
            raise SystemExit(f"cannot sweep {key!r}; choose from {', '.join(OVERRIDABLE)}")
        dims[key] = [v for v in vals.split(",") if v]
    return dims


def main() -> None:
    from . import store
    from .pipeline import run_question
    from .report import build, collect, render

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", nargs="*", help="question ids (default: all in questions.json)")
    ap.add_argument("--configs", nargs="*", default=DEFAULT_SET)
    ap.add_argument("--sweep", nargs="*", help="key=v1,v2 ... applied to every configuration except current")
    ap.add_argument("--level", choices=settings.LEVELS)
    ap.add_argument("--mode", choices=settings.STRICTNESS, default="strict")
    ap.add_argument("--ai-judge", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    questions = store.load_questions()
    if args.list:
        print("Configurations:")
        for k, c in PRESETS.items():
            print(f"  {k:22} {c.label}: {c.description}")
        print("\nQuestions:")
        for q in questions:
            print(f"  {q['id']:8} [{q.get('level')}] {q['question'][:90]}")
        return
    if args.questions:
        questions = [q for q in questions if q["id"] in set(args.questions)]
    configs = sweep(args.configs, _parse_sweep(args.sweep)) if args.sweep else resolve(args.configs)
    since = time.strftime("%Y%m%d-%H%M%S")
    print(f"{len(questions)} questions x {len(configs)} configurations, device={settings.DEVICE}")
    for qi, q in enumerate(questions, 1):
        level = args.level or q.get("level") or "FCPS-I"
        print(f"\n[{qi}/{len(questions)}] {q['id']} ({level}, {args.mode}): {q['question'][:80]}", flush=True)
        out = run_question(q, level, args.mode, configs, progress=lambda m: print("   ", m, flush=True),
                           ai_judge=args.ai_judge)
        for r in out["results"]:
            m = r.get("metrics", {})
            rm = ((r.get("retrieval_metrics") or {}).get("after_reranking") or {})
            print(f"    {r['configuration'][:44]:46} {'ERROR ' + r['error'][:60] if r.get('error') else ''}"
                  f"total {m.get('effective_total_ms', m.get('total_ms', 0)) / 1000:5.1f}s  rerank {(m.get('rerank_ms') or 0) / 1000:5.2f}s  "
                  f"first relevant {rm.get('rank_first_relevant', '–')}", flush=True)
    print()
    print(render(build(collect(since))))


if __name__ == "__main__":
    main()
