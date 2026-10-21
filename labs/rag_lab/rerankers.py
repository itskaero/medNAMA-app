"""Cross-encoder rerankers, loaded once per process and scored as production scores them.

Scoring rules (kept identical to production and to labs/evaluation/reranker):
  * the text scored is the candidate's 1,500-character focus window (production _focus_text)
  * each candidate is scored against every retrieval query and keeps its best score
  * raw logits (activation = identity). sentence-transformers applies a sigmoid to MedCPT and the BGE
    models by default; ordering is unchanged by it, but saturated sigmoids tie (labs/evaluation/reranker
    found 7% of MedCPT scores tied at 1.0 when rounded). Logits never tie that way.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from . import settings

_models: dict[str, Any] = {}
_load_info: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
MAX_LENGTH = 512
BATCH = 32


def _revision(name: str) -> str | None:
    try:
        from huggingface_hub import scan_cache_dir
        for repo in scan_cache_dir().repos:
            if repo.repo_id == name:
                return sorted(repo.revisions, key=lambda r: r.last_modified)[-1].commit_hash
    except Exception:
        return None
    return None


def get(key: str):
    """The model for a key in settings.MODELS (msmarco, medcpt, bge_base, bge_v2_m3)."""
    with _lock:
        if key in _models:
            return _models[key]
        import psutil
        import torch
        from sentence_transformers import CrossEncoder

        name = settings.MODELS[key]
        rss0 = psutil.Process().memory_info().rss
        t0 = time.perf_counter()
        model = CrossEncoder(name, max_length=MAX_LENGTH, device="cuda" if settings.DEVICE == "cuda" else "cpu")
        model.model.eval()
        torch.manual_seed(0)
        _load_info[key] = {
            "model": name, "revision": _revision(name), "device": settings.DEVICE,
            "load_ms": round((time.perf_counter() - t0) * 1000, 1),
            "rss_increase_mb": round((psutil.Process().memory_info().rss - rss0) / 2**20, 1),
            "params_m": round(sum(p.numel() for p in model.model.parameters()) / 1e6, 1),
        }
        _models[key] = model
        return model


def load_info() -> dict[str, dict[str, Any]]:
    return dict(_load_info)


def score(key: str, queries: list[str], texts: list[str]) -> tuple[list[float], float]:
    """Best score per text over the queries, and the milliseconds spent scoring (load excluded)."""
    if not texts:
        return [], 0.0
    import torch

    model = get(key)
    t0 = time.perf_counter()
    best = [float("-inf")] * len(texts)
    for q in queries:
        out = model.predict([(q, t) for t in texts], batch_size=BATCH, show_progress_bar=False,
                            activation_fn=torch.nn.Identity())
        best = [max(b, float(s)) for b, s in zip(best, out)]
    if settings.DEVICE == "cuda":
        torch.cuda.synchronize()
    return best, (time.perf_counter() - t0) * 1000
