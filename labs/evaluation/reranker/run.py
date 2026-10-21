"""MedNAMA reranker benchmark: one entry point for every stage. Read-only against production.

Run from the repository root with the GPU venv (or any env with the backend requirements):

    .venv-gpu/Scripts/python.exe labs/evaluation/reranker/run.py check            # datasets + gold vs corpus
    .venv-gpu/Scripts/python.exe labs/evaluation/reranker/run.py retrieve         # candidate pools (LLM rewrite once)
    .venv-gpu/Scripts/python.exe labs/evaluation/reranker/run.py verify-pool      # pool == production's, checked
    .venv-gpu/Scripts/python.exe labs/evaluation/reranker/run.py rerank           # every model x input format (GPU)
    .venv-gpu/Scripts/python.exe labs/evaluation/reranker/run.py cpu-latency      # NAS-like CPU timings (subset)
    .venv-gpu/Scripts/python.exe labs/evaluation/reranker/run.py evaluate         # results.json / .csv + report
    .venv-gpu/Scripts/python.exe labs/evaluation/reranker/run.py all              # retrieve..evaluate

Ground truth (see README.md):
    run.py annotate q022                       # show predicate matches in the pool and the corpus
    run.py annotate q022 --accept 123 456      # record verified chunk ids (status: verified)
    run.py annotate q022 --clear               # back to predicate labels
    run.py sheet export                        # ground_truth_review.csv: one row per proposed chunk
    run.py sheet import                        # apply the y/n decisions from the sheet
"""

from __future__ import annotations

import os

# Models load from the local cache only: a benchmark must not pick up a newer revision mid-run.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench.common import BENCH_DIR, CACHE, DATASETS, load_items, read_json, use_backend, validate_items, write_json  # noqa: E402

use_backend()
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

SHEET = BENCH_DIR / "ground_truth_review.csv"


def cmd_check(args) -> None:
    from bench.candidates import corpus_matches
    from app.database import SessionLocal

    items = load_items()
    problems = validate_items(items)
    print(f"{len(items)} items: " + ", ".join(f"{k} {sum(i.kind == k for i in items)}" for k in DATASETS))
    for p in problems:
        print("PROBLEM", p)
    db = SessionLocal()
    zero = []
    for it in items:
        n = len(corpus_matches(db, it.gold, 200))
        ds = {d.label: len(corpus_matches(db, d.gold, 200)) for d in it.distractors}
        if n == 0 or 0 in ds.values():
            zero.append((it.id, n, ds))
    print(f"gold predicates with no match anywhere in the corpus: {len(zero)}")
    for z in zero:
        print("  ", z)
    sys.exit(1 if problems or zero else 0)


def cmd_retrieve(args) -> None:
    from bench.candidates import build_all

    items = load_items()
    t0 = time.monotonic()
    build_all(items, refresh_rewrites=args.refresh_rewrites, only=set(args.only) if args.only else None,
              rebuild=getattr(args, "rebuild", False))
    print(f"retrieval done in {time.monotonic() - t0:.0f}s -> {CACHE / 'candidates.json'}")


def cmd_verify_pool(args) -> None:
    """Run the real search() and check every candidate it reranks is in the benchmark pool, in RRF order."""
    import app.retrieval as R
    from app.database import SessionLocal

    cands = read_json(CACHE / "candidates.json")
    rewrites = read_json(CACHE / "rewrites.json", {})
    if not cands:
        sys.exit("run `retrieve` first")
    captured: list[list[int]] = []
    original_rerank, original_rewrite = R.RetrievalService.rerank_chunks, R.rewrite_query

    def spy(self, query, parent_chunks, limit=5, texts=None):
        if not captured or captured[-1] is not None:
            captured.append([p.id for p in parent_chunks])
        return original_rerank(self, query, parent_chunks, limit=limit, texts=texts)

    R.RetrievalService.rerank_chunks = spy
    R.rewrite_query = lambda q, context_hint="", level=None: (rewrites.get(q) or {}).get("rewrite")
    db = SessionLocal()
    ok = bad = 0
    try:
        ids = list(cands["items"])[: args.limit]
        for item_id in ids:
            entry = cands["items"][item_id]
            captured.clear()
            R.retrieval_service.search(db, entry["queries"][0], deep_rerank=False)
            db.rollback()
            prod = captured[0] if captured else []
            pool = [c["id"] for c in entry["candidates"] if c["source"] == "retrieval"]
            missing = [p for p in prod if p not in pool]
            ordered = [p for p in pool if p in prod]
            same_order = ordered == [p for p in prod if p in pool][: len(ordered)] or sorted(ordered) == sorted(prod)
            if missing:
                bad += 1
                print(f"MISMATCH {item_id}: production reranked {len(prod)}, {len(missing)} not in pool: {missing[:5]}")
            else:
                ok += 1
                print(f"ok {item_id}: production's {len(prod)} candidates are all in the benchmark pool "
                      f"of {len(pool)}{'' if same_order else ' (order differs)'}")
    finally:
        R.RetrievalService.rerank_chunks, R.rewrite_query = original_rerank, original_rewrite
    print(f"\n{ok} match, {bad} mismatch")
    sys.exit(1 if bad else 0)


def cmd_rerank(args) -> None:
    from bench.rerankers import MODELS, MODES, score_items, timed_head

    cands = read_json(CACHE / "candidates.json")
    if not cands:
        sys.exit("run `retrieve` first")
    models = args.models or list(MODELS)
    modes = args.modes or list(MODES)
    device = args.device
    for key in models:
        for mode in modes:
            t0 = time.monotonic()
            out = score_items(key, mode, cands, device=device)
            m = out["meta"]
            print(f"{key:10} {mode:10} {len(out['items'])} items in {time.monotonic() - t0:.0f}s "
                  f"(load {m['load_seconds']}s, gpu peak {m['gpu_peak_mb']} MB, rss +{m['rss_delta_mb']} MB)", flush=True)
    # Stage-2 latency as production runs it: the second model on stage 1's top 8 only.
    s1 = (read_json(CACHE / f"scores_msmarco_passage{'' if device == 'cuda' else '_' + device}.json") or {}).get("items")
    if s1:
        heads = {}
        for key in ("medcpt", "bge_base", "bge_v2_m3"):
            heads[key] = timed_head(key, cands, {i: v["scores"] for i, v in s1.items()}, 8, device=device)
        from bench.rerankers import scores_path
        write_json(scores_path("_heads", "passage", device), {"items": heads})
        print("stage-2 head latencies recorded")


def cmd_cpu_latency(args) -> None:
    """The same models on the CPU, a subset of items, passage mode: closer to the NAS than the GPU."""
    from bench.rerankers import MODELS, scores_path, score_items, timed_head

    cands = read_json(CACHE / "candidates.json")
    ids = [i for i in cands["items"]][: args.limit]
    subset = {"items": {i: cands["items"][i] for i in ids}}
    for key in args.models or list(MODELS):
        t0 = time.monotonic()
        out = score_items(key, "passage", subset, device="cpu", threads=args.threads)
        print(f"{key:10} cpu: {len(ids)} items in {time.monotonic() - t0:.0f}s, rss +{out['meta']['rss_delta_mb']} MB", flush=True)
    s1 = (read_json(scores_path("msmarco", "passage", "cpu")) or {}).get("items", {})
    heads = {k: timed_head(k, subset, {i: v["scores"] for i, v in s1.items()}, 8, device="cpu")
             for k in ("medcpt", "bge_base", "bge_v2_m3")}
    write_json(scores_path("_heads", "passage", "cpu"), {"items": heads})


def cmd_evaluate(args) -> None:
    import platform

    import sentence_transformers
    import torch
    import transformers

    from bench.evaluate import evaluate, summarise, write_outputs
    from bench.report import write_report

    items = load_items()
    cands = read_json(CACHE / "candidates.json")
    evaluated = evaluate(items, cands, device="cuda")
    summary = summarise(evaluated["results"])
    cpu = {}
    from bench.rerankers import MODELS, load_scores
    for key in MODELS:
        s = load_scores(key, "passage", "cpu")
        if s:
            cpu[key] = {"meta": s["meta"], "seconds": [v["seconds"] for v in s["items"].values()]}
    heads_cpu = (load_scores("_heads", "passage", "cpu") or {}).get("items", {})
    meta = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": platform.python_version(), "torch": torch.__version__,
        "sentence_transformers": sentence_transformers.__version__, "transformers": transformers.__version__,
        "cuda": torch.version.cuda, "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "cpu": platform.processor(), "candidates": cands.get("meta", {}),
        "cpu_latency": cpu, "cpu_heads": heads_cpu,
    }
    write_outputs(evaluated, summary, meta)
    write_report(evaluated, summary, meta)
    print(f"wrote results.json, results.csv, RERANKER_BENCHMARK.md ({len(evaluated['results'])} items)")


def cmd_all(args) -> None:
    cmd_retrieve(args)
    cmd_rerank(args)
    cmd_evaluate(args)


# ── ground truth ──────────────────────────────────────────────────────────────

def _dataset_of(item_id: str) -> Path:
    for path in DATASETS.values():
        if path.exists() and any(r["id"] == item_id for r in json.loads(path.read_text(encoding="utf-8"))):
            return path
    sys.exit(f"no item {item_id}")


def _set_verified(item_id: str, ids: list[int], who: str) -> None:
    from app.database import SessionLocal
    from app.models import Chunk

    path = _dataset_of(item_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    db = SessionLocal()
    rows = {c.id: c for c in db.query(Chunk).filter(Chunk.id.in_(ids)).all()} if ids else {}
    missing = [i for i in ids if i not in rows]
    if missing:
        sys.exit(f"chunk ids not in the database: {missing}")
    if any(rows[i].parent_id is not None for i in ids):
        sys.exit("give PARENT chunk ids (the ones the benchmark lists); child ids are never ranked")
    for r in data:
        if r["id"] != item_id:
            continue
        r["relevant_chunk_ids"] = sorted(ids)
        r["annotation"] = ({"status": "verified", "basis": "chunk_ids", "verified_by": who,
                            "verified_at": time.strftime("%Y-%m-%d")} if ids else
                           {"status": "unverified", "basis": "predicate"})
        if ids and "relevant_book" in r:
            first = rows[sorted(ids)[0]]
            r["relevant_book"] = first.book.title if first.book else ""
            r["relevant_chapter"] = first.chapter or ""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{item_id}: {'verified ' + str(sorted(ids)) if ids else 'cleared, back to the predicate'}")


def cmd_annotate(args) -> None:
    from bench.candidates import corpus_matches, nearest_matches
    from app.database import SessionLocal
    from app.models import Chunk
    from app.retrieval import retrieval_service

    item = next((i for i in load_items() if i.id == args.item), None)
    if item is None:
        sys.exit(f"no item {args.item}")
    if args.accept or args.clear:
        _set_verified(item.id, [] if args.clear else args.accept, args.who)
        return
    db = SessionLocal()
    print(f"{item.id} [{item.kind}] {item.query}\n  gold: {item.gold.describe()}\n  status: {item.annotation}")
    entry = (read_json(CACHE / "candidates.json") or {}).get("items", {}).get(item.id)
    if entry:
        print("\nIn the retrieval pool (rank = RRF order):")
        for r, c in enumerate(entry["candidates"], 1):
            if item.gold.matches(c["parent_text"]) or c["id"] in item.relevant_chunk_ids:
                print(f"  #{r:<3} id {c['id']:<8} {c['book']} p.{c['page']} | {(c['chapter'] or '')[:60]}")
                print(f"        {' '.join(c['text'].split())[:240]}")
    ids = corpus_matches(db, item.gold, 300)
    near = nearest_matches(db, ids, retrieval_service._embed_query(item.query), 10)
    print(f"\nWhole corpus: {len(ids)}{'+' if len(ids) >= 300 else ''} parents match; nearest to the query:")
    for c in db.query(Chunk).filter(Chunk.id.in_(near)).all():
        print(f"  id {c.id:<8} {c.book.title if c.book else '?'} p.{c.page_number} | {(c.chapter or '')[:60]}")
        print(f"        {' '.join(c.content.split())[:240]}")
    print(f"\nRecord the passages that answer the question:  run.py annotate {item.id} --accept <id> [<id> ...]")


def cmd_sheet(args) -> None:
    if args.action == "export":
        cands = read_json(CACHE / "candidates.json")
        if not cands:
            sys.exit("run `retrieve` first")
        with open(SHEET, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["item_id", "query", "chunk_id", "book", "page", "chapter", "passage", "predicate_match",
                        "decision (y/n)"])
            for it in load_items():
                entry = cands["items"].get(it.id)
                if not entry:
                    continue
                for c in entry["candidates"]:
                    match = it.gold.matches(c["parent_text"])
                    if match or c["id"] in it.relevant_chunk_ids:
                        w.writerow([it.id, it.query, c["id"], c["book"], c["page"], c["chapter"],
                                    " ".join(c["parent_text"].split())[:600], "yes" if match else "no",
                                    "y" if c["id"] in it.relevant_chunk_ids else ""])
        print(f"wrote {SHEET}: mark the last column y (answers the question) or n, then `sheet import`")
    else:
        decisions: dict[str, list[int]] = {}
        with open(SHEET, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                d = (row.get("decision (y/n)") or "").strip().lower()
                if d in ("y", "yes"):
                    decisions.setdefault(row["item_id"], []).append(int(row["chunk_id"]))
                elif d in ("n", "no"):
                    decisions.setdefault(row["item_id"], [])
        for item_id, ids in decisions.items():
            if ids:
                _set_verified(item_id, ids, args.who)
        print(f"{sum(1 for v in decisions.values() if v)} items verified from the sheet")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    r = sub.add_parser("retrieve")
    r.add_argument("--refresh-rewrites", action="store_true", help="call the LLM again instead of replaying")
    r.add_argument("--only", nargs="*")
    r.add_argument("--rebuild", action="store_true", help="redo items already in the cache")
    v = sub.add_parser("verify-pool")
    v.add_argument("--limit", type=int, default=15)
    rr = sub.add_parser("rerank")
    rr.add_argument("--models", nargs="*")
    rr.add_argument("--modes", nargs="*")
    rr.add_argument("--device", default="cuda")
    c = sub.add_parser("cpu-latency")
    c.add_argument("--models", nargs="*")
    c.add_argument("--limit", type=int, default=15)
    c.add_argument("--threads", type=int, default=4)
    sub.add_parser("evaluate")
    a = sub.add_parser("all")
    a.add_argument("--refresh-rewrites", action="store_true")
    a.add_argument("--only", nargs="*")
    a.add_argument("--models", nargs="*")
    a.add_argument("--modes", nargs="*")
    a.add_argument("--device", default="cuda")
    an = sub.add_parser("annotate")
    an.add_argument("item")
    an.add_argument("--accept", nargs="*", type=int)
    an.add_argument("--clear", action="store_true")
    an.add_argument("--who", default="annotator")
    sh = sub.add_parser("sheet")
    sh.add_argument("action", choices=["export", "import"])
    sh.add_argument("--who", default="annotator")
    args = p.parse_args()
    {"check": cmd_check, "retrieve": cmd_retrieve, "verify-pool": cmd_verify_pool, "rerank": cmd_rerank,
     "cpu-latency": cmd_cpu_latency, "evaluate": cmd_evaluate, "all": cmd_all, "annotate": cmd_annotate,
     "sheet": cmd_sheet}[args.cmd](args)


if __name__ == "__main__":
    main()
