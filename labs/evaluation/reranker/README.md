# Reranker benchmark

An offline harness that compares cross-encoder rerankers on MedNAMA's own retrieval pools. It reads the
production database and calls production's retrieval functions; it changes nothing in production
(no code, settings, embeddings, prompts or data). How the pipeline works and where the harness taps
in: [`docs/reranker_benchmark_architecture.md`](../../docs/reranker_benchmark_architecture.md).

## Layout

```
labs/evaluation/reranker/
  run.py                        every stage (check, retrieve, verify-pool, rerank, cpu-latency, evaluate,
                                annotate, sheet)
  questions.json                67 questions, 14 subjects
  medical_confusion_tests.json  18 contests: the correct concept against named look-alike concepts
  terminology_tests.json        19 bare-term queries (GALT, p-ANCA, AICA...) with near-miss distractors
  bench/common.py               gold predicates, datasets, text normalisation
  bench/candidates.py           stage 1: candidate pools from production's retrieval primitives
  bench/rerankers.py            stage 2: raw scores per model and input format
  bench/evaluate.py             stage 3: arms, metrics, contests, failure classes
  bench/report.py               RERANKER_BENCHMARK.md (tables generated; prose from analysis_notes.md)
  tools/make_questions.py       provenance of the starter question set
  analysis_notes.md             the hand-written interpretation spliced into the report
  RERANKER_BENCHMARK.md         the report            RECOMMENDATION.md   the conclusion
  results.json / results.csv    per-question rankings, scores, ranks, latency, failure class
  cache/                        candidate pools, recorded rewrites, raw scores (not committed: contains book text)
```

## Running it

Needs the dev database up (Docker: `mednama-dev-db` on :5434), the LLM key in the root `.env` (production's
query rewrite is an LLM call), and the GPU venv. From the repository root:

```bash
PY=.venv-gpu/Scripts/python.exe        # Windows Git Bash; any env with backend/requirements.txt works

# 0. Install (only if the venv lacks them): torch, sentence-transformers, psutil, pandas
# 1. Models: the four rerankers must be in the Hugging Face cache. run.py forces offline mode, so a
#    missing model fails loudly instead of downloading a different revision mid-benchmark:
$PY -c "from huggingface_hub import snapshot_download as s; [s(m) for m in ['cross-encoder/ms-marco-MiniLM-L-6-v2','ncbi/MedCPT-Cross-Encoder','BAAI/bge-reranker-v2-m3','BAAI/bge-reranker-base']]"
# 2. Datasets: validate, and check every gold predicate exists somewhere in the corpus
$PY labs/evaluation/reranker/run.py check
# 3. Retrieval: build and cache the pools (records each LLM rewrite once; resumable)
$PY labs/evaluation/reranker/run.py retrieve
# 4. Prove the pool contains what production reranks
$PY labs/evaluation/reranker/run.py verify-pool --limit 20
# 5. Reranking: every model x {passage, structured} on the GPU, plus stage-2 head timings
$PY labs/evaluation/reranker/run.py rerank
# 6. CPU timings on a subset (closer to the NAS, which has no GPU)
$PY labs/evaluation/reranker/run.py cpu-latency --limit 15 --threads 4
# 7. Metrics, results.json / results.csv, RERANKER_BENCHMARK.md
$PY labs/evaluation/reranker/run.py evaluate
```

`run.py all` runs 3, 5 and 7. Re-running `evaluate` alone is instant: it reads cached scores.

## Ground truth

Chunk ids were **not** guessed. Each item carries a *gold predicate*, the terms the answer passage must
contain:

```json
"gold": {"all": ["howell jolly"], "any": [["splen", "asplen", "hyposplen"]]}
```

Matching ignores case, hyphens and spacing, maps Greek letters to names (PPARγ = "ppar gamma"), anchors
terms at word starts, and `=term` means a whole word. `run.py check` confirms every predicate (and every
distractor) matches at least one passage in the 25-book corpus; two that did not (the Nagler reaction,
tetanolysin) were replaced, and the notes in `medical_confusion_tests.json` say so.

A predicate is a reproducible proxy, not a human judgement. To replace it with verified chunk ids:

```bash
$PY labs/evaluation/reranker/run.py annotate q022                  # pool + corpus passages that match, with ids
$PY labs/evaluation/reranker/run.py annotate q022 --accept 120334 120335 --who "Dr X"
$PY labs/evaluation/reranker/run.py annotate q022 --clear          # back to the predicate

# or in bulk, in a spreadsheet:
$PY labs/evaluation/reranker/run.py sheet export                   # ground_truth_review.csv
#   ...mark the last column y / n...
$PY labs/evaluation/reranker/run.py sheet import --who "Dr X"
$PY labs/evaluation/reranker/run.py evaluate                       # verified items now use their ids
```

A verified item is scored on its ids alone (status `verified`, with who and when). The report states how
many items rest on each basis. Give **parent** chunk ids: the tool lists only those.

## Determinism and what is recorded

* The LLM rewrite is recorded once in `cache/rewrites.json` and replayed (`--refresh-rewrites` re-asks).
* Candidate pools are frozen in `cache/candidates.json`; every reranker scores exactly those candidates.
* Models: fp32, eval mode, `torch.manual_seed(0)`, batch 32, `max_length` 512, Hugging Face offline mode.
* Scores are **raw logits** (`activation_fn=Identity`), stored unrounded. sentence-transformers applies a
  sigmoid to MedCPT and the BGE rerankers by default; MedCPT's large logits saturated it, and rounding then
  tied 7% of its scores at exactly 1.0 (see RERANKER_BENCHMARK.md, Performance).
* Recorded per model in `results.json` → `meta.models`: name, cache revision, parameters, device,
  batch size, max length, load time, GPU peak memory, process RSS growth. Also library versions, GPU,
  pool configuration.
* GPU timings synchronise CUDA around each call and exclude model load and the first warm-up call.

## Configuration

| Setting | Value | Where |
|---|---|---|
| Pool | top 50 by RRF + every list's top 3 | `bench/candidates.py` `POOL_SIZE` |
| Retrieval lists | 60 rows, vector + keyword, question + rewrite | production `CANDIDATE_LIMIT` |
| Reranker input | the 1,500-char focus window | production `_focus_text` |
| Query scoring | max over question and rewrite | as production |
| Production stage 2 | MedCPT on stage 1's top 8 | `settings.reranker_second_stage_top_n` |
| Miss threshold | no relevant passage in the top 5 | `bench/evaluate.py` `CONTEXT_K` |
| Contest injection | 2 nearest real passages per concept, when absent | `bench/candidates.py` `INJECT_PER_SIDE` |
