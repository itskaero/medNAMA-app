"""Shared pieces: paths, the backend import, text normalisation and gold predicates, the datasets.

Gold predicates
---------------
A predicate is what the answer passage must say, written as terms rather than chunk ids, so it can be
checked against the real corpus instead of being guessed:

    {"all": ["howell jolly"],                  every term must appear
     "any": [["splen", "asplen"]],             at least one term from each group
     "none": ["heinz"]}                        none of these may appear (rarely needed)

Matching is case-insensitive and tolerant of hyphens, slashes and spaces ("Howell-Jolly",
"howell jolly", "howelljolly" all match "howell jolly"). Greek letters match their names
("PPARγ" matches "ppar gamma"). A term is anchored at the start of a word, so "splen" matches
"splenectomy" but not "asplenic" (list that separately). A term written "=pca" must be a whole word.

The same predicate compiles to a Python regex (for candidates in memory) and to a PostgreSQL regex
(for searching the whole corpus), so "is the answer anywhere in the library?" and "is it in this
pool?" use one definition.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent.parent          # labs/evaluation/reranker
REPO = BENCH_DIR.parent.parent.parent   # labs/evaluation/reranker -> repo root
BACKEND = REPO / "backend"
CACHE = BENCH_DIR / "cache"
DATASETS = {
    "question": BENCH_DIR / "questions.json",
    "confusion": BENCH_DIR / "medical_confusion_tests.json",
    "terminology": BENCH_DIR / "terminology_tests.json",
}


def use_backend() -> None:
    """Make `import app...` resolve to the production backend (read-only use)."""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


# ── text normalisation ────────────────────────────────────────────────────────

_GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "κ": "kappa", "μ": "mu"}
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {ord("’"): "'", ord("‘"): "'", ord("“"): '"', ord("”"): '"'}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_DASHES).translate(_QUOTES).lower()
    for g, name in _GREEK.items():
        text = text.replace(g, name)
    return " ".join(text.split())


_SEP_PY = r"[\s\-/]*"
_SEP_SQL = r"[[:space:]/‐‑–—-]*"
_GREEK_NAMES = {name: g for g, name in _GREEK.items()}


def _term_parts(term: str) -> tuple[list[str], bool]:
    whole = term.startswith("=")
    words = normalise(term.lstrip("=")).replace("-", " ").replace("/", " ").split()
    return words, whole


def _ligature_tolerant(escaped_word: str, space: str) -> str:
    """PDF extraction splits words at lost ligatures ("circumfl ex", "fi ve"); production's keyword
    search tolerates this too (retrieval.LIGATURES). Allow an optional space after fi/fl/ff."""
    return re.sub(r"(ff|fi|fl)(?=.)", lambda m: m.group(1) + space + "?", escaped_word)


def term_regex(term: str) -> str:
    words, whole = _term_parts(term)
    body = _SEP_PY.join(_ligature_tolerant(re.escape(w), r"\s") for w in words)
    return r"(?<![a-z0-9])" + body + (r"s?(?![a-z0-9])" if whole else "")   # "=pca" also matches "PCAs"


def term_sql(term: str) -> str:
    """PostgreSQL ARE for one term (used with `~*`)."""
    words, whole = _term_parts(term)

    def word(w: str) -> str:
        out = re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", w)
        for name, g in _GREEK_NAMES.items():   # the corpus keeps the Greek letter
            out = out.replace(name, f"({name}|{g})")
        return _ligature_tolerant(out, "[[:space:]]")

    body = _SEP_SQL.join(word(w) for w in words)
    lead = r"\m" if words and words[0][:1].isalnum() else ""
    return lead + body + (r"s?\M" if whole else "")


@dataclass
class Gold:
    all: list[str] = field(default_factory=list)
    any: list[list[str]] = field(default_factory=list)
    none: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, spec: dict | None) -> "Gold":
        spec = spec or {}
        return cls(list(spec.get("all") or []), [list(g) for g in spec.get("any") or []], list(spec.get("none") or []))

    def empty(self) -> bool:
        return not (self.all or self.any)

    def matches(self, text: str) -> bool:
        if self.empty():
            return False
        t = normalise(text)
        if not all(re.search(term_regex(x), t) for x in self.all):
            return False
        if not all(any(re.search(term_regex(x), t) for x in group) for group in self.any):
            return False
        return not any(re.search(term_regex(x), t) for x in self.none)

    def sql(self, column: str = "content") -> tuple[str, dict[str, str]]:
        """A WHERE fragment and its parameters (patterns are bound, never interpolated)."""
        parts, params = [], {}

        def bind(term: str) -> str:
            key = f"p{len(params)}"
            params[key] = term_sql(term)
            return f"{column} ~* :{key}"

        parts += [bind(x) for x in self.all]
        parts += ["(" + " OR ".join(bind(x) for x in group) + ")" for group in self.any]
        parts += [f"NOT ({bind(x)})" for x in self.none]
        return " AND ".join(parts) or "FALSE", params

    def describe(self) -> str:
        bits = [" + ".join(self.all)] if self.all else []
        bits += ["(" + " | ".join(g) + ")" for g in self.any]
        if self.none:
            bits.append("not (" + " | ".join(self.none) + ")")
        return " + ".join(b for b in bits if b)


# ── datasets ──────────────────────────────────────────────────────────────────

@dataclass
class Distractor:
    label: str
    gold: Gold


@dataclass
class Item:
    id: str
    kind: str                      # question | confusion | terminology
    query: str
    subject: str
    difficulty: str
    tags: list[str]
    gold: Gold
    distractors: list[Distractor]
    relevant_chunk_ids: list[int]
    annotation: dict[str, Any]
    correct_label: str = ""
    notes: str = ""

    @property
    def verified(self) -> bool:
        return bool(self.relevant_chunk_ids) and self.annotation.get("status") == "verified"


def load_items(kinds: tuple[str, ...] = ("question", "confusion", "terminology")) -> list[Item]:
    items: list[Item] = []
    for kind in kinds:
        path = DATASETS[kind]
        if not path.exists():
            continue
        for raw in json.loads(path.read_text(encoding="utf-8")):
            correct = raw.get("correct") or {}
            items.append(Item(
                id=raw["id"], kind=kind, query=raw["query"],
                subject=raw.get("subject", ""), difficulty=raw.get("difficulty", ""),
                tags=list(raw.get("tags") or []),
                gold=Gold.parse(raw.get("gold") or correct.get("gold")),
                distractors=[Distractor(d["label"], Gold.parse(d["gold"])) for d in raw.get("distractors") or []],
                relevant_chunk_ids=[int(x) for x in raw.get("relevant_chunk_ids") or []],
                annotation=dict(raw.get("annotation") or {}),
                correct_label=correct.get("label", ""),
                notes=raw.get("notes", ""),
            ))
    ids = [i.id for i in items]
    dupes = {x for x in ids if ids.count(x) > 1}
    if dupes:
        raise ValueError(f"duplicate item ids: {sorted(dupes)}")
    return items


def validate_items(items: list[Item]) -> list[str]:
    """Problems that would make a result meaningless, before any model runs."""
    problems = []
    for it in items:
        if it.gold.empty() and not it.relevant_chunk_ids:
            problems.append(f"{it.id}: no gold predicate and no verified chunk ids")
        if it.kind in ("confusion", "terminology") and not it.distractors:
            problems.append(f"{it.id}: a {it.kind} test needs distractors")
        for d in it.distractors:
            if d.gold.empty():
                problems.append(f"{it.id}: distractor '{d.label}' has an empty predicate")
        for term in it.gold.all + [t for g in it.gold.any for t in g]:
            try:
                re.compile(term_regex(term))
            except re.error as e:
                problems.append(f"{it.id}: bad term {term!r}: {e}")
    return problems


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
