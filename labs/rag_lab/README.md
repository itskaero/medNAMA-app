# MedNAMA RAG Lab

An isolated playground for one question at a time: run it through several retrieval / reranking /
context / generation configurations on the **same** corpus, candidate pool and LLM, read the answers
side by side, and score them by hand. Answer quality, judged by a person, is the measure that matters;
retrieval metrics and the optional AI judge are supporting evidence.

**Production is not modified.** No backend file, prompt, schema, endpoint or frontend file was changed
to build this. The lab imports production's functions read-only, and the few observations it needs
(token usage, production's reranking times) are taken by wrapping methods *inside the lab's own
process* for the duration of a call.

## Run it

From the repository root, with the dev database up (Docker) and the LLM key in the root `.env`:

```bash
PY=.venv-gpu/Scripts/python.exe                     # any env with backend requirements + uvicorn

$PY -m labs.rag_lab.server                                # UI on http://127.0.0.1:8765
$PY -m labs.rag_lab.benchmark --list                      # configurations and benchmark questions
$PY -m labs.rag_lab.benchmark --questions lab001 lab003   # run questions through the default six
$PY -m labs.rag_lab.benchmark --configs msmarco_medcpt msmarco_bgebase --sweep pool_size=16,32,50 top_k=5,6,8
$PY -m labs.rag_lab.benchmark --configs msmarco_medcpt --sweep generation_mode=direct,plan,full coverage=none,check_retrieve
$PY -m labs.rag_lab.report                                # aggregate every stored run + human score
$PY -m labs.rag_lab.improve                               # what to change in MedNAMA's RAG, and the evidence
```

### The improvement report

`python -m labs.rag_lab.improve`, or **Improvement report** in the UI, writes `data/IMPROVEMENT_REPORT.md`. It is
rule-based (no LLM writes or judges it) and turns every stored run into a list of changes to make, each
with its evidence, sample size and a confidence:

1. **Where answers lose the evidence**: retrieval, reranking, context selection, generation, citation or
   question understanding. The human's failure class is used where scored; otherwise the stage is
   recomputed from the evidence trail with the current rules. It also lists cases where one reranker lost
   evidence that another kept on the same pool.
2. **What each setting changed**: pairs of answers to the same question in the same run that differ in
   exactly one setting (pool size, top-K, reranker, coverage, generation mode, prompt...). For each pair
   it gives the change in human score, rank of the first relevant passage, evidence reaching the LLM,
   latency and tokens.
3. **Recurring problems**:
   - heading-only, bibliography and index chunks that reached the LLM;
   - citations the validator had to strip;
   - components the passages most often lacked (corpus or query gaps);
   - the slowest stage;
   - reranker memory against the 4 GB server, measured after real scoring.
4. **Recommendations**, highest priority first. They include the verified production findings in
   `known_findings.json`; add your own there.
5. **What to run next**: settings with no data yet, with the exact benchmark command to fill each gap.

Runs recorded before the warm-up step existed are left out of the timing and memory figures.

`RAG_LAB_DEVICE=cpu` (the default) hides the GPU so timings and RAM resemble the GPU-less server;
`RAG_LAB_DEVICE=cuda` is faster for exploration. Models load from the local cache only
(`HF_HUB_OFFLINE=1`); override any of them with `RAG_LAB_MODEL_MSMARCO`, `RAG_LAB_MODEL_MEDCPT`,
`RAG_LAB_MODEL_BGE_BASE`, `RAG_LAB_MODEL_BGE_M3`. The UI binds to 127.0.0.1 only.

## Architecture

```
question ─┬─ CURRENT ──────── production generate_answer() unchanged (its own search, prompt, validation)
          │                   observed: pool handed to stage 1, stage-1/2 times, context, tokens, RAM
          │
          └─ every other configuration, on shared inputs:
               [question understanding]  ── one LLM call per run, reused        (optional)
               queries  ── original | production rewrite | understanding+rewrite | ≤4 intent queries
               retrieval ── production vector + FTS lists → RRF (k, weights configurable) → pool N
                            one pool per (query mode, N, RRF settings) per run: identical candidates
               [chunk-quality flags] ── heading-only / index / bibliography / very short / TOC  (optional)
               reranking ── none | ms-marco | ms-marco → MedCPT | ms-marco → BGE base | BGE v2-m3
                            + production's soft exam-level bonus
               context ── production select_diverse (book diversity) or plain top-K
                          → production expand_context (neighbour paragraphs) → production select_figures
               [coverage] ── which required components the passages state; optional targeted retrieval
               [answer plan] ── structure + evidence requirements only (no reasoning text)
               generation ── experimental prompt (STRICT | TUTOR) or production's prompt on this context
               validation ── production validate_generation / _strip_unknown_refs
          ↓
     data/runs/<run>/<config>.json   +   human scores (separate)   +   AI judge (separate, labelled)
```

| File | Role |
|---|---|
| `settings.py` | env configuration, device choice, backend import path, git state |
| `configs.py` | the `Config` dataclass (every switch) and presets A-H |
| `retrieval.py` | queries per mode, RRF variants, candidate pool, chunk-quality flags |
| `rerankers.py` | cross-encoders, loaded once, scored as production scores (focus window, max over queries, raw logits) |
| `llm_stages.py` | question understanding, coverage, answer plan, AI judge (JSON only) |
| `generation.py` | the experimental prompt and citation validation |
| `production_runner.py` | CURRENT, and production's prompt on a lab context |
| `pipeline.py` | runs one question through configurations; measures everything; warm-up |
| `metrics.py` | Recall@1/5/10/16, MRR, nDCG@10 against gold evidence; failure-stage suggestion |
| `resources.py` | per-stage time, CPU time, RSS before/peak, LLM token usage |
| `store.py` | JSON storage; human and AI evaluations in separate folders |
| `server.py`, `static/index.html` | local UI |
| `benchmark.py`, `report.py` | batch runs and the report |

### Reused from production (read-only)

`app.retrieval`: `rewrite_query`, `RetrievalService._embed_query / vector_search / keyword_search /
expand_context / select_figures`, `select_diverse`, `_focus_text`, `looks_like_index_page`,
`CANDIDATE_LIMIT`, `PER_LIST_TOP`, `INTENT_SUBJECTS`, `LEVEL_SUBJECT_BONUS`, `ContextChunk`,
`SearchResult`. `app.generation`: `generate_answer`, `validate_generation`, `_strip_unknown_refs`,
`strip_all_citations`. `app.llm.chat_completion` (the same LLM for every configuration).
`app.exam_levels` (level guidance, preferred subjects, intent). `app.database.SessionLocal` and the
existing 25-book corpus: nothing is copied or re-embedded. From `labs/evaluation/reranker`: the gold
predicate matcher.

### Reproduced (so it can vary)

The RRF loop of `search()`: k, weights and pool size are configurable. With k = 60, equal weights and
pool 16 it is production's pool. **Not reproduced:** production's exam-level extras inside `search()`
(per-book probes and per-book pool heads when a level is set). The lab applies the exam level as the
soft ordering bonus only; CURRENT includes the extras because it *is* production.

## Configurations

| Name | What it is |
|---|---|
| `current` | A. Production, unchanged |
| `retrieval_only` | B. Hybrid retrieval + rewrite + RRF, no reranker |
| `msmarco` | C. + ms-marco MiniLM |
| `msmarco_medcpt` | D. + MedCPT re-orders the top 8 (production's shape) |
| `msmarco_bgebase` | E. + BGE base re-orders the top 8 |
| `bge_v2_m3` | F. BGE v2-m3 over the pool (~1.6 GB RAM: development machine only) |
| `no_rerank_pool50` | G. No reranker, pool 50 |
| `rrf_k30`, `rrf_vector_heavy`, `rrf_keyword_heavy` | H. RRF variants (with ms-marco → MedCPT) |

Any of these can be overridden (UI "Overrides" or `--sweep`): `pool_size` (16/32/50), `top_k`
(5/6/8), `query_mode`, `understanding`, `coverage` (`none`/`check`/`check_retrieve`), `chunk_filter`,
`generation_mode` (`direct` = 1, `plan` = 2, `full` = 3: understanding + coverage + plan), `prompt`
(`experimental`/`production`), `context_strategy`, `level_bonus`. Overrides never apply to CURRENT.

## Measurements and how to read them

- **Latency.** `effective_total_ms` is what a configuration would cost run alone. It is its own wall time
  plus the shared work it reused: the retrieval pool and question understanding. Compare this one;
  `total_ms` favours whichever configuration ran later.
- **Warm-up.** Before anything is timed, every model the run needs is loaded and the database touched.
  Without it, the first configuration absorbed model loading and cold caches: CURRENT measured 177 s of
  "retrieval" instead of ~15 s. Warm-up time and what it loaded are recorded in `meta.json`.
- **RAM.** RSS before and peak per configuration. Models stay loaded once a configuration has used them,
  so per-model RAM is in `meta.json → reproducibility.model_load_info`. On the 4 GB server, BGE v2-m3
  (~1.6 GB) is not viable whatever its quality.
- **CPU time.** Process user + system time during the configuration, all threads.
- **Tokens.** From the API's `usage`, per LLM call (understanding, coverage, plan, answer, judge).
- **Hardware recorded.** The development machine measured here is an i7-8700 with an RTX 2060 and
  16 GB of RAM. The production server is weaker (i5 4th generation, 4 GB). Treat lab CPU timings as a
  lower bound for production.

## Ground truth and evaluation

- `questions.json` holds `id`, `question`, `level`, `intent`, `expected_components`, `gold_notes`,
  and optionally `gold` (a term predicate) or `relevant_chunk_ids` for retrieval metrics.
  - The 12 starter questions reuse gold predicates from `labs/evaluation/reranker` where they overlap.
  - Their expected components are drafts written by Claude, **not clinician-reviewed**.
  - Add questions in the UI or by editing the file.
- **Human scores** are the measure. Each configuration is scored on accuracy, completeness, relevance,
  grounding, citation correctness, reasoning quality, exam-level fit and overall (0–5). There are also
  six PASS/FAIL checks, a failure class and notes. They are stored in `data/evaluations_human`, with
  the full edit history kept.
- **The AI judge** is optional. It is stored separately in `data/evaluations_ai` and always labelled
  "AI evaluation — not ground truth".
- **Failure classes:**
  - retrieval: the evidence was never in the pool;
  - reranking: it was in the pool but ranked below the context cut;
  - context selection: it was ranked in but not sent to the LLM;
  - generation;
  - citation;
  - question understanding.

  The lab pre-selects one of the first three from the evidence trail when it can. A human confirms it.
- **The report** names a winner against CURRENT only when at least 10 questions have paired human
  "overall" scores (paired bootstrap, 95% interval). Below that it says so.

## Reproducibility

Every run records:
- the git commit and branch, and whether the tree had uncommitted changes;
- the full configuration;
- model names and Hugging Face cache revisions;
- the LLM model names and temperature (0);
- prompt versions and a hash of the experimental prompt;
- retrieval settings, pool size and top-K, exam level and strict/tutor mode;
- the question and a timestamp;
- the exact retrieval queries used.

The LLM rewrite is not bit-for-bit deterministic, and neither is production retrieval: ties in
full-text scores and approximate vector search shuffle borderline candidates. Within a run, every
configuration shares one pool, so comparisons inside a run are exact.
