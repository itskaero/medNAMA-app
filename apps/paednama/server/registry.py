"""The CPSP Paediatrics curriculum as data (data/cpsp_paeds.json, built by scripts/build_registry.py)."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from . import settings

STAGES = ("imm", "fcps2")


@lru_cache(maxsize=1)
def registry() -> dict[str, Any]:
    return json.loads(settings.REGISTRY.read_text(encoding="utf-8"))


def stage(stage_id: str) -> dict[str, Any] | None:
    return registry()["stages"].get(stage_id)


def summary() -> dict[str, Any]:
    """Each stage's exam components, without the long lists: what the setup and the exam page show."""
    r = registry()
    return {"source": r["source"], "note": r["note"], "stages": [
        {"id": sid, "title": st["title"], "training_years": st["training_years"], "eligibility": st["eligibility"],
         "theory": st["theory"], "clinical": st["clinical"], "rotations": st.get("rotations"),
         "systems": len(st["syllabus"]), "topics": sum(len(s["topics"]) for s in st["syllabus"]),
         "mini_cex": len(st["mini_cex"]["topics"]), "dops": len(st["dops"]["procedures"])}
        for sid, st in r["stages"].items()]}


def find_topic(stage_id: str, topic_id: str) -> tuple[dict, dict] | None:
    """(system, topic) for a topic id in a stage."""
    st = stage(stage_id)
    for system in (st or {}).get("syllabus", []):
        for topic in system["topics"]:
            if topic["id"] == topic_id:
                return system, topic
    return None


def search(stage_id: str, q: str, limit: int = 30) -> list[dict[str, Any]]:
    """Topics whose name, sub-topics or system contain every word of q."""
    words = [w for w in q.lower().split() if w]
    out = []
    for system in (stage(stage_id) or {}).get("syllabus", []):
        for topic in system["topics"]:
            hay = " ".join([topic["name"], system["system"], *topic.get("subtopics", [])]).lower()
            if words and all(w in hay for w in words):
                out.append({"system": system["system"], "system_id": system["id"], **topic})
                if len(out) >= limit:
                    return out
    return out
