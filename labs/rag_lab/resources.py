"""Measuring what a configuration costs: wall time per stage, CPU time, RAM, and LLM tokens.

* RAM: resident set size before the run and the peak during it, sampled every 20 ms by a thread.
  Models loaded by an earlier configuration in the same process stay loaded, so "RAM before" grows
  over a run; the model registry records each model's own load cost separately.
* CPU: process user + system time spent during the run (all threads).
* Tokens: production's chat_completion() does not return usage, so the lab wraps the OpenAI
  client *inside its own process* and records `response.usage` per call. No backend file changes.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any

import psutil

from . import settings  # noqa: F401  (device + backend path first)

_PROC = psutil.Process()
_usage_local = threading.local()


class Meter:
    """Times named stages and tracks RAM/CPU for one configuration run."""

    def __init__(self) -> None:
        self.stages: dict[str, float] = {}
        self.rss_before = _PROC.memory_info().rss
        self.rss_peak = self.rss_before
        self._cpu0 = _PROC.cpu_times()
        self._t0 = time.perf_counter()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()

    def _sample(self) -> None:
        while not self._stop.is_set():
            try:
                self.rss_peak = max(self.rss_peak, _PROC.memory_info().rss)
            except Exception:
                pass
            self._stop.wait(0.02)

    @contextmanager
    def stage(self, name: str):
        t = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + (time.perf_counter() - t) * 1000

    def finish(self) -> dict[str, Any]:
        self._stop.set()
        self._thread.join(timeout=1)
        cpu = _PROC.cpu_times()
        return {
            "stages_ms": {k: round(v, 1) for k, v in self.stages.items()},
            "total_ms": round((time.perf_counter() - self._t0) * 1000, 1),
            "cpu_time_s": round((cpu.user - self._cpu0.user) + (cpu.system - self._cpu0.system), 2),
            "rss_before_mb": round(self.rss_before / 2**20, 1),
            "rss_peak_mb": round(self.rss_peak / 2**20, 1),
            "rss_increase_mb": round((self.rss_peak - self.rss_before) / 2**20, 1),
        }


# ── LLM token usage (lab process only) ────────────────────────────────────────

def _usage_sink() -> list[dict[str, Any]]:
    if not hasattr(_usage_local, "calls"):
        _usage_local.calls = []
    return _usage_local.calls


@contextmanager
def capture_llm_usage():
    """Collect usage of every LLM call made inside the block (this thread)."""
    sink = _usage_sink()
    start = len(sink)
    out: list[dict[str, Any]] = []
    try:
        yield out
    finally:
        out.extend(sink[start:])
        del sink[start:]


_installed = False


def install_usage_spy() -> None:
    """Wrap the clients production's llm.get_client() hands out, recording usage per call."""
    global _installed
    if _installed:
        return
    import app.llm as llm

    original = llm.get_client

    class _Completions:
        def __init__(self, inner, role):
            self._inner, self._role = inner, role

        def create(self, **kwargs):
            t = time.perf_counter()
            resp = self._inner.create(**kwargs)
            usage = getattr(resp, "usage", None)
            _usage_sink().append({
                "role": self._role, "model": kwargs.get("model"),
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
                "ms": round((time.perf_counter() - t) * 1000, 1),
            })
            return resp

    class _Chat:
        def __init__(self, inner, role):
            self.completions = _Completions(inner.completions, role)

    class _Client:
        def __init__(self, inner, role):
            self._inner, self.chat = inner, _Chat(inner.chat, role)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def spied(role: str = "chat"):
        return _Client(original(role), role)

    llm.get_client = spied
    _installed = True


def summarise_usage(calls: list[dict[str, Any]]) -> dict[str, Any]:
    def total(key: str) -> int | None:
        vals = [c[key] for c in calls if c.get(key) is not None]
        return sum(vals) if vals else None
    return {"calls": len(calls), "prompt_tokens": total("prompt_tokens"),
            "completion_tokens": total("completion_tokens"), "total_tokens": total("total_tokens"),
            "llm_ms": round(sum(c["ms"] for c in calls), 1), "by_call": calls}
