"""JSON storage: one folder per run, one file per configuration; human and AI evaluations apart.

    data/runs/<run_id>/meta.json                 question, level, mode, reproducibility record
    data/runs/<run_id>/<configuration>.json      everything one configuration did and produced
    data/evaluations_human/<run_id>/<configuration>.json   human scores (history kept)
    data/evaluations_ai/<run_id>/<configuration>.json      AI judge output, labelled "not ground truth"

Human scores never live in the same file as AI scores, so one can never be mistaken for the other.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from . import settings


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.=,\-\[\]]+", "_", name)[:150]


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def _read(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_result(run_id: str, rec: dict[str, Any]) -> None:
    _write(settings.RUNS / run_id / f"{_safe(rec['configuration'])}.json", rec)


def save_meta(run_id: str, meta: dict[str, Any]) -> None:
    _write(settings.RUNS / run_id / "meta.json", meta)


def load_run(run_id: str) -> dict[str, Any]:
    folder = settings.RUNS / run_id
    meta = _read(folder / "meta.json", {})
    results = [_read(p) for p in sorted(folder.glob("*.json")) if p.name != "meta.json"]
    order = {name: i for i, name in enumerate(meta.get("configurations", []))}
    results.sort(key=lambda r: order.get(r.get("configuration"), 999))
    for r in results:
        r["human_evaluation"] = load_human(run_id, r["configuration"])
        r["ai_evaluation"] = load_ai(run_id, r["configuration"])
    return {"meta": meta, "results": results}


def list_runs() -> list[dict[str, Any]]:
    out = []
    if not settings.RUNS.exists():
        return out
    for folder in sorted(settings.RUNS.iterdir(), reverse=True):
        meta = _read(folder / "meta.json")
        if meta:
            scored = settings.HUMAN_EVALS / folder.name
            out.append({**{k: meta.get(k) for k in ("run_id", "question_id", "question", "level", "strictness",
                                                    "timestamp", "configurations")},
                        "human_scored": len(list(scored.glob("*.json"))) if scored.exists() else 0})
    return out


def save_human(run_id: str, config: str, evaluation: dict[str, Any]) -> dict[str, Any]:
    path = settings.HUMAN_EVALS / run_id / f"{_safe(config)}.json"
    current = _read(path, {"history": []})
    entry = {**evaluation, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": "human"}
    current["history"].append(entry)
    current["latest"] = entry
    _write(path, current)
    return current


def load_human(run_id: str, config: str) -> dict[str, Any] | None:
    data = _read(settings.HUMAN_EVALS / run_id / f"{_safe(config)}.json")
    return data.get("latest") if data else None


def save_ai_eval(run_id: str, config: str, evaluation: dict[str, Any]) -> None:
    _write(settings.AI_EVALS / run_id / f"{_safe(config)}.json",
           {**evaluation, "kind": "ai", "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S")})


def load_ai(run_id: str, config: str) -> dict[str, Any] | None:
    return _read(settings.AI_EVALS / run_id / f"{_safe(config)}.json")


def load_questions() -> list[dict[str, Any]]:
    return _read(settings.QUESTIONS, [])


def add_question(item: dict[str, Any]) -> dict[str, Any]:
    qs = load_questions()
    if not item.get("id"):
        n = 1 + max([int(m.group(1)) for q in qs if (m := re.match(r"lab(\d+)$", str(q.get("id"))))] or [0])
        item["id"] = f"lab{n:03d}"
    if any(q.get("id") == item["id"] for q in qs):
        raise ValueError(f"question id {item['id']} exists")
    qs.append(item)
    _write(settings.QUESTIONS, qs)
    return item
