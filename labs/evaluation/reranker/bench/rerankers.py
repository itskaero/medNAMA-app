"""Stage 2: score the frozen candidate sets with every cross-encoder, in two input formats.

Models (sentence-transformers CrossEncoder, max_length 512, eval mode, fp32):

    msmarco    cross-encoder/ms-marco-MiniLM-L-6-v2   production stage 1 (22M params)
    medcpt     ncbi/MedCPT-Cross-Encoder              production stage 2 (110M, PubMedBERT)
    bge_v2_m3  BAAI/bge-reranker-v2-m3                strongest BGE reranker that fits the PC GPU (568M)
    bge_base   BAAI/bge-reranker-base                 the BGE size that could live on the NAS (278M)

Every model scores (query, text) for every query of the item - the original and production's
rewrite - and keeps the max per candidate, exactly as search() does. The arms built from these
scores (including production's two-stage "current") are assembled in evaluate.py; this module only
produces and caches raw scores, so metrics can be recomputed without re-running a model.

Input formats:
    passage     the candidate's focus text (what production's rerankers score today)
    structured  the same text under the metadata the corpus actually has:
                    [BOOK] <title>
                    [CHAPTER] <first level of the chapter path>     (only when the path has levels)
                    [SECTION] <rest of the path, joined with " → ">  (or the whole path if one level)
                    [PAGE] <page>
                    [PASSAGE] <focus text>
                No field is invented: an absent value drops its line.
"""

from __future__ import annotations

import gc
import os
import time
from typing import Any

import numpy as np

from .common import CACHE, read_json, write_json

MODELS: dict[str, str] = {
    "msmarco": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "medcpt": "ncbi/MedCPT-Cross-Encoder",
    "bge_v2_m3": "BAAI/bge-reranker-v2-m3",
    "bge_base": "BAAI/bge-reranker-base",
}
MODES = ("passage", "structured")
# Raw logits for every model. sentence-transformers applies a Sigmoid to models whose config names no
# activation (MedCPT and both BGE rerankers), and MedCPT's large logits saturate it: 7% of its scores
# came out as exactly 1.0, tied at the top of the ranking, so the order fell back to candidate index.
# A monotonic activation cannot change a ranking, but saturation can; logits never saturate.
MAX_LENGTH = 512
BATCH_SIZE = 32


def structured_text(c: dict[str, Any]) -> str:
    lines = []
    if c.get("book"):
        lines.append(f"[BOOK] {c['book']}")
    path = [p.strip() for p in (c.get("chapter") or "").split(" > ") if p.strip()]
    if len(path) > 1:
        lines.append(f"[CHAPTER] {path[0]}")
        lines.append(f"[SECTION] {' → '.join(path[1:])}")
    elif path:
        lines.append(f"[SECTION] {path[0]}")
    if c.get("page") is not None:
        lines.append(f"[PAGE] {c['page']}")
    lines.append(f"[PASSAGE] {c['text']}")
    return "\n".join(lines)


def candidate_text(c: dict[str, Any], mode: str) -> str:
    return c["text"] if mode == "passage" else structured_text(c)


def scores_path(model_key: str, mode: str, device: str) -> Any:
    suffix = "" if device == "cuda" else f"_{device}"
    return CACHE / f"scores_{model_key}_{mode}{suffix}.json"


def _revision(name: str) -> str | None:
    try:
        from huggingface_hub import scan_cache_dir
        for repo in scan_cache_dir().repos:
            if repo.repo_id == name:
                return sorted(repo.revisions, key=lambda r: r.last_modified)[-1].commit_hash
    except Exception:
        return None
    return None


def load(model_key: str, device: str):
    import torch
    from sentence_transformers import CrossEncoder

    torch.manual_seed(0)
    model = CrossEncoder(MODELS[model_key], max_length=MAX_LENGTH, device=device)
    model.model.eval()
    return model


def _sync(device: str) -> None:
    if device == "cuda":
        import torch
        torch.cuda.synchronize()


def score_items(model_key: str, mode: str, candidates: dict[str, Any], device: str = "cuda",
                only: set[str] | None = None, limit: int | None = None, threads: int | None = None) -> dict[str, Any]:
    """Score every item's pool; returns and caches {meta, items: {id: {scores, seconds, per_query}}}."""
    import psutil
    import torch

    if threads:
        torch.set_num_threads(threads)
    proc = psutil.Process(os.getpid())
    rss0 = proc.memory_info().rss
    if device == "cuda":
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
    t_load = time.monotonic()
    model = load(model_key, device)
    load_s = time.monotonic() - t_load
    model.predict([("warm up", "warm up")] * 4, batch_size=BATCH_SIZE)   # first-call kernels excluded from timings
    _sync(device)

    out = {"items": {}}
    ids = [i for i in candidates["items"] if not only or i in only]
    if limit:
        ids = ids[:limit]
    for item_id in ids:
        entry = candidates["items"][item_id]
        texts = [candidate_text(c, mode) for c in entry["candidates"]]
        per_query, seconds = [], 0.0
        for q in entry["queries"]:
            _sync(device)
            t0 = time.perf_counter()
            s = model.predict([(q, t) for t in texts], batch_size=BATCH_SIZE, show_progress_bar=False,
                              activation_fn=torch.nn.Identity())
            _sync(device)
            seconds += time.perf_counter() - t0
            per_query.append([float(x) for x in np.asarray(s).ravel()])
        best = np.max(np.array(per_query), axis=0).tolist() if per_query else []
        out["items"][item_id] = {"scores": best, "seconds": round(seconds, 5),
                                 "per_query": per_query,
                                 "n_pairs": len(texts) * len(entry["queries"])}
    out["meta"] = {
        "model_key": model_key, "model": MODELS[model_key], "revision": _revision(MODELS[model_key]),
        "mode": mode, "device": device, "dtype": "float32", "batch_size": BATCH_SIZE, "max_length": MAX_LENGTH,
        "activation": "identity (raw logits)",
        "load_seconds": round(load_s, 2), "torch_threads": torch.get_num_threads(),
        "gpu_peak_mb": round(torch.cuda.max_memory_allocated() / 2**20, 1) if device == "cuda" else None,
        "rss_delta_mb": round((proc.memory_info().rss - rss0) / 2**20, 1),
        "params_m": round(sum(p.numel() for p in model.model.parameters()) / 1e6, 1),
        "scored_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_json(scores_path(model_key, mode, device), out)
    del model
    gc.collect()
    if device == "cuda":
        torch.cuda.empty_cache()
    return out


def timed_head(model_key: str, candidates: dict[str, Any], order_by: dict[str, list[float]], top_n: int,
               device: str = "cuda", mode: str = "passage", limit: int | None = None) -> dict[str, float]:
    """Latency of production's stage 2 as it really runs: the second model on stage 1's top N only."""
    model = load(model_key, device)
    model.predict([("warm up", "warm up")] * 4, batch_size=BATCH_SIZE)
    out = {}
    ids = [i for i in candidates["items"] if i in order_by]
    if limit:
        ids = ids[:limit]
    for item_id in ids:
        entry = candidates["items"][item_id]
        s1 = order_by[item_id]
        head = sorted(range(len(s1)), key=lambda i: s1[i], reverse=True)[:top_n]
        texts = [candidate_text(entry["candidates"][i], mode) for i in head]
        seconds = 0.0
        for q in entry["queries"]:
            _sync(device)
            t0 = time.perf_counter()
            model.predict([(q, t) for t in texts], batch_size=BATCH_SIZE, show_progress_bar=False)
            _sync(device)
            seconds += time.perf_counter() - t0
        out[item_id] = round(seconds, 5)
    del model
    gc.collect()
    return out


def load_scores(model_key: str, mode: str, device: str = "cuda") -> dict[str, Any] | None:
    return read_json(scores_path(model_key, mode, device))
