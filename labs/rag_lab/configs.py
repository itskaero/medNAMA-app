"""Experiment configurations: every switch in one dataclass, and the named presets (A-H).

A configuration is plain data, saved verbatim with every result, so any run can be repeated.
`apply_overrides()` lets the UI and the benchmark sweep one dimension (pool size, top-K, query
mode, question understanding, coverage, chunk filter, generation mode, prompt) across presets.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

QUERY_MODES = ("original", "rewrite", "qu_rewrite", "multi")
COVERAGE_MODES = ("none", "check", "check_retrieve")
GENERATION_MODES = ("direct", "plan", "full")          # 1, 2, 3 in the brief
CONTEXT_STRATEGIES = ("production_diverse", "top_k")
PROMPTS = ("experimental", "production")


@dataclass
class Config:
    name: str
    label: str
    description: str = ""
    # A: call production's generate_answer() as it is (its own retrieval, prompt, validation)
    production: bool = False
    # retrieval
    query_mode: str = "rewrite"           # original | rewrite | qu_rewrite | multi
    pool_size: int = 16                   # candidates handed to reranking (production: 16 + list heads)
    rrf_k: int = 60
    rrf_weight_vector: float = 1.0
    rrf_weight_keyword: float = 1.0
    # reranking
    stage1: str | None = None             # msmarco | medcpt | bge_base | bge_v2_m3 | None (keep RRF order)
    stage2: str | None = None             # re-orders stage 1's top stage2_top_n
    stage2_top_n: int = 8
    level_bonus: bool = True              # production's soft subject preference for the exam level
    chunk_filter: bool = False            # experimental: demote heading-only / index / bibliography chunks
    # context selection
    top_k: int = 6                        # production chat uses 6
    context_strategy: str = "production_diverse"
    expand_neighbours: bool = True        # production expand_context
    figures: bool = True                  # production select_figures
    # optional stages
    understanding: bool = False           # question understanding (structured JSON)
    coverage: str = "none"                # none | check | check_retrieve
    generation_mode: str = "direct"       # direct | plan | full
    prompt: str = "experimental"          # experimental | production (production's prompt on this context)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def retrieval_key(self) -> tuple:
        """Configurations with the same key share one candidate pool within a run."""
        return (self.query_mode, self.pool_size, self.rrf_k, self.rrf_weight_vector, self.rrf_weight_keyword,
                self.understanding or self.query_mode in ("qu_rewrite", "multi"))


PRESETS: dict[str, Config] = {c.name: c for c in [
    Config("current", "CURRENT (production)", production=True,
           description="Production's generate_answer() unchanged: its own retrieval (rewrite, hybrid, RRF, 16 + "
                       "heads, ms-marco -> MedCPT top 8, level probes and bonus, diversity, expansion, figures) "
                       "and its own prompt and citation validation."),
    Config("retrieval_only", "RETRIEVAL ONLY", stage1=None,
           description="Hybrid retrieval (vector + FTS, rewrite, RRF), no reranker."),
    Config("msmarco", "MS-MARCO", stage1="msmarco",
           description="Hybrid retrieval -> RRF -> ms-marco MiniLM."),
    Config("msmarco_medcpt", "MS-MARCO → MEDCPT", stage1="msmarco", stage2="medcpt",
           description="Production's reranking shape on the lab's shared pool."),
    Config("msmarco_bgebase", "MS-MARCO → BGE-BASE", stage1="msmarco", stage2="bge_base",
           description="ms-marco, then BGE base re-orders the top 8."),
    Config("bge_v2_m3", "BGE-V2-M3", stage1="bge_v2_m3",
           description="BGE v2-m3 over the whole pool (heavy: ~1.6 GB RAM; not viable on the 4 GB server)."),
    Config("no_rerank_pool50", "NO RERANKER, POOL 50", stage1=None, pool_size=50,
           description="Is reranking needed at all? RRF order over a larger pool, diversity chooses the context."),
    Config("rrf_k30", "RRF k=30 (ms-marco → MedCPT)", stage1="msmarco", stage2="medcpt", rrf_k=30,
           description="Sharper RRF: top ranks of each list count for more."),
    Config("rrf_vector_heavy", "RRF vector-heavy (ms-marco → MedCPT)", stage1="msmarco", stage2="medcpt",
           rrf_weight_vector=1.5, rrf_weight_keyword=1.0,
           description="RRF with semantic (vector) lists weighted 1.5x."),
    Config("rrf_keyword_heavy", "RRF keyword-heavy (ms-marco → MedCPT)", stage1="msmarco", stage2="medcpt",
           rrf_weight_vector=1.0, rrf_weight_keyword=1.5,
           description="RRF with full-text (keyword) lists weighted 1.5x."),
]}

DEFAULT_SET = ["current", "retrieval_only", "msmarco", "msmarco_medcpt", "msmarco_bgebase", "bge_v2_m3"]

OVERRIDABLE = {
    "pool_size": int, "top_k": int, "query_mode": str, "understanding": bool, "coverage": str,
    "chunk_filter": bool, "generation_mode": str, "prompt": str, "rrf_k": int, "context_strategy": str,
    "level_bonus": bool, "expand_neighbours": bool, "figures": bool, "stage2_top_n": int,
}


def _coerce(key: str, value: Any) -> Any:
    kind = OVERRIDABLE[key]
    if kind is bool:
        return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
    return kind(value)


def apply_overrides(cfg: Config, overrides: dict[str, Any] | None) -> Config:
    """A copy with overrides applied; the name records them so results never collide."""
    overrides = {k: _coerce(k, v) for k, v in (overrides or {}).items() if k in OVERRIDABLE and v not in (None, "")}
    if not overrides:
        return cfg
    if cfg.production:
        return cfg          # production is run as-is; its knobs are not the lab's to turn
    suffix = ",".join(f"{k}={v}" for k, v in sorted(overrides.items()))
    out = replace(cfg, **overrides)
    out.name = f"{cfg.name}[{suffix}]"
    out.label = f"{cfg.label} [{suffix}]"
    validate(out)
    return out


def validate(cfg: Config) -> None:
    assert cfg.query_mode in QUERY_MODES, cfg.query_mode
    assert cfg.coverage in COVERAGE_MODES, cfg.coverage
    assert cfg.generation_mode in GENERATION_MODES, cfg.generation_mode
    assert cfg.context_strategy in CONTEXT_STRATEGIES, cfg.context_strategy
    assert cfg.prompt in PROMPTS, cfg.prompt
    assert cfg.pool_size >= 1 and cfg.top_k >= 1


def resolve(names: list[str], overrides: dict[str, Any] | None = None) -> list[Config]:
    out = []
    for n in names or DEFAULT_SET:
        if n not in PRESETS:
            raise KeyError(f"unknown configuration {n!r}; known: {', '.join(PRESETS)}")
        out.append(apply_overrides(PRESETS[n], overrides))
    return out


def sweep(names: list[str], dims: dict[str, list[Any]]) -> list[Config]:
    """Cartesian product of override values over the named presets (production is not swept)."""
    import itertools

    keys = list(dims)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*(dims[k] for k in keys))] or [{}]
    seen, out = set(), []
    for n in names or DEFAULT_SET:
        for combo in combos:
            cfg = apply_overrides(PRESETS[n], combo)
            if cfg.name not in seen:
                seen.add(cfg.name)
                out.append(cfg)
    return out

