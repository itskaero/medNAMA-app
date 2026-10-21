"""Planned targets: how many questions each TOS unit should end up with, and where that number came from.

A *unit* is what one generation batch writes about: a topic (its sub-topics are listed in the prompt so questions
spread across them), or a subject / specialty with nothing under it. A *note leaf* is a sub-topic, or a topic with no
sub-topics, or a bare subject: one revision note each.

Every planned number carries its source, shown in the UI:

  official   the TOS prints a count for this item: count x multiplier
  derived    a share of the nearest printed count above it (split evenly, largest remainder, after any printed
             minimum such as "at least two MCQs from each topic")
  override   an operator set this target (audited)
  internal   the TOS prints no count anywhere on this branch (FCPS prospectuses): base_per_topic x multiplier

Multipliers are internal bank-size targets (5x by default, 10x expanded), not exam requirements.
Pure functions over plain dicts, so the arithmetic is unit-tested without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

UNIT_LEVELS = ("topic",)
DEFAULT_DIFFICULTY = {1: 10, 2: 25, 3: 35, 4: 20, 5: 10}   # percent of each unit's target per level


@dataclass
class Plan:
    planned: int
    source: str             # official | derived | override | internal | rollup
    note: str | None = None


def split_even(total: int, n: int) -> list[int]:
    """total split into n parts differing by at most one (largest remainder, earlier parts first)."""
    if n <= 0:
        return []
    base, extra = divmod(max(0, total), n)
    return [base + (1 if i < extra else 0) for i in range(n)]


def allocate_difficulty(total: int, dist: dict[int, float] | None = None) -> dict[int, int]:
    """total questions across difficulty 1-5 by percentage, largest remainder, summing exactly to total."""
    dist = {int(k): float(v) for k, v in (dist or DEFAULT_DIFFICULTY).items() if float(v) > 0}
    if not dist or total <= 0:
        return {k: 0 for k in range(1, 6)}
    weight = sum(dist.values())
    exact = {k: total * v / weight for k, v in dist.items()}
    out = {k: int(x) for k, x in exact.items()}
    left = total - sum(out.values())
    for k in sorted(exact, key=lambda k: (-(exact[k] - out[k]), k))[:left]:
        out[k] += 1
    return {k: out.get(k, 0) for k in range(1, 6)}


def _children(nodes: Iterable[dict]) -> dict[Any, list[dict]]:
    kids: dict[Any, list[dict]] = {}
    for n in sorted(nodes, key=lambda n: n.get("position", 0)):
        kids.setdefault(n.get("parent_id"), []).append(n)
    return kids


def is_unit(n: dict, kids: dict) -> bool:
    return n["level"] in UNIT_LEVELS or (n["level"] in ("subject", "specialty") and not kids.get(n["id"]))


def is_note_leaf(n: dict, kids: dict) -> bool:
    return n["level"] in ("subject", "topic", "subtopic") and not kids.get(n["id"])


def _stated(n: dict, multiplier: float) -> tuple[int | None, str | None]:
    if n.get("target_override") is not None:
        return int(n["target_override"]), "override"
    if n.get("official_count") is not None:
        return int(round(n["official_count"] * multiplier)), "official"
    return None, None


def _counted_below(n: dict, kids: dict, multiplier: float) -> int | None:
    """Sum of the top-most stated counts beneath n (x multiplier), or None if nothing below is stated."""
    total, found = 0, False
    for c in kids.get(n["id"], []):
        v, _ = _stated(c, multiplier)
        if v is None:
            v = _counted_below(c, kids, multiplier)
        if v is not None:
            total, found = total + v, True
    return total if found else None


def compute(nodes: list[dict], multiplier: float = 5.0, base_per_topic: int = 2) -> dict[Any, Plan]:
    """Planned target for every node. Units get their own number; everything above is the sum of its units."""
    kids = _children(nodes)
    plans: dict[Any, Plan] = {}

    def walk(n: dict, budget: int | None, source: str | None) -> None:
        own, own_src = _stated(n, multiplier)
        if own is not None:
            budget, source = own, own_src
        if is_unit(n, kids):
            if budget is None:
                below = _counted_below(n, kids, multiplier)
                if below is not None:
                    plans[n["id"]] = Plan(below, "official", "sum of the counts printed for its sub-topics")
                else:
                    plans[n["id"]] = Plan(int(round(base_per_topic * multiplier)), "internal",
                                          "no MCQ count printed; internal base per topic")
            else:
                plans[n["id"]] = Plan(budget, source or "derived")
            return
        children = kids.get(n["id"], [])
        if budget is None:
            for c in children:
                walk(c, None, None)
            return
        stated = {c["id"]: _stated(c, multiplier)[0] for c in children}
        for c in children:   # a part with no count of its own may still have counted parts inside it
            if stated[c["id"]] is None:
                stated[c["id"]] = _counted_below(c, kids, multiplier)
        fixed = sum(v for v in stated.values() if v is not None)
        free = [c for c in children if stated[c["id"]] is None]
        remaining = max(0, budget - fixed)
        floor = int(round((n.get("min_per_child") or 0) * multiplier))
        shares = [floor] * len(free)
        if floor and floor * len(free) > remaining:
            shares = split_even(remaining, len(free))
        else:
            for i, extra in enumerate(split_even(remaining - floor * len(free), len(free))):
                shares[i] += extra
        for c in children:
            if stated[c["id"]] is not None:
                walk(c, None, None)
        for c, share in zip(free, shares):
            walk(c, share, "derived")

    for root in kids.get(None, []):
        walk(root, None, None)

    def rollup(n: dict) -> int:
        if n["id"] in plans:
            return plans[n["id"]].planned
        total = sum(rollup(c) for c in kids.get(n["id"], []))
        own, _ = _stated(n, multiplier)
        note = None
        if own is not None and own != total:
            note = f"its parts are planned at {total}, not the {own} its own count gives"
        plans[n["id"]] = Plan(total, "rollup", note)
        return total

    for root in kids.get(None, []):
        rollup(root)
    return plans


def units_under(nodes: list[dict], scope_id: Any) -> list[dict]:
    kids = _children(nodes)
    out: list[dict] = []

    def walk(n: dict) -> None:
        if is_unit(n, kids):
            out.append(n)
            return
        for c in kids.get(n["id"], []):
            walk(c)

    by_id = {n["id"]: n for n in nodes}
    if scope_id in by_id:
        walk(by_id[scope_id])
    return out


def note_leaves_under(nodes: list[dict], scope_id: Any) -> list[dict]:
    kids = _children(nodes)
    out: list[dict] = []

    def walk(n: dict) -> None:
        if is_note_leaf(n, kids):
            out.append(n)
        for c in kids.get(n["id"], []):
            walk(c)

    by_id = {n["id"]: n for n in nodes}
    if scope_id in by_id:
        walk(by_id[scope_id])
    return out


def subtopics_of(nodes: list[dict], unit_id: Any) -> list[str]:
    kids = _children(nodes)
    names: list[str] = []

    def walk(n: dict) -> None:
        for c in kids.get(n["id"], []):
            names.append(c["name"])
            walk(c)

    walk({"id": unit_id})
    return names


def batches_for(need_by_difficulty: dict[int, int], batch_size: int) -> list[tuple[int, int, int]]:
    """[(difficulty, seq, target)] for the questions still needed; seq numbers each difficulty's batches from 0."""
    out = []
    for d in range(1, 6):
        need = max(0, int(need_by_difficulty.get(d, 0)))
        seq = 0
        while need > 0:
            take = min(batch_size, need)
            out.append((d, seq, take))
            need -= take
            seq += 1
    return out
