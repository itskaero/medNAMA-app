# Reranker benchmark: the current RAG pipeline, and where the benchmark taps into it

Written from reading the code on 2026-10-06 (branch `claude/medical-rag-coverage-gaps-12n5us`, after
`ac3ccb5`). Nothing in production was changed to write this. File and line references are to `backend/`.

## 1. Pipeline at a glance

```
question
  │
  ├─ rewrite_query()            fast LLM turns the question into ~30 textbook keywords   app/retrieval.py:272
  │                             (skipped when the question already looks like keywords)
  ▼
queries = [original, rewritten?]
  │  for each query:
  │    ├─ vector_search()       bge-large-en-v1.5, pgvector HNSW over CHILD chunks, top 60  app/retrieval.py:423
  │    └─ keyword_search()      Postgres FTS over CHILD chunks, AND then OR fallback, top 60 app/retrieval.py:475
  ▼
RRF fusion (k = 60) onto PARENT ids                                                      app/retrieval.py:675-691
  │  [level set only] extra per-book probes for the programme's books                     app/retrieval.py:700-722
  ▼
rerank pool = top 16 parents by RRF  +  top 3 of each individual list
              [level set only] + up to 8 per-book best parents                            app/retrieval.py:731-757
  │  index pages dropped (looks_like_index_page)
  │
  │  ◄──────────── BENCHMARK INTERCEPTION POINT: the candidate parents and their
  │                 "focus" texts, before any cross-encoder has scored them
  ▼
stage 1: cross-encoder/ms-marco-MiniLM-L-6-v2 scores every candidate's focus text
         (1,500-char window of the parent around the matched child), max over queries     app/retrieval.py:764-770
  ▼
stage 2: ncbi/MedCPT-Cross-Encoder re-orders stage 1's top 8, max over queries;
         the stored scores stay stage 1's (MIN_RERANK_SCORE = -7 is calibrated on them)   app/retrieval.py:772-786
  ▼
[level set only] +0.75 ordering bonus for books of the programme's subjects               app/retrieval.py:793-812
  ▼
select_diverse(): drop scores < -7, give every book within 4.0 of the top a slot,
                  at most 2 slots per book, keep rank order                                app/retrieval.py:361-393
  ▼
expand_context(): grow each chosen parent into a block of relevant same-page neighbours
                  (neighbours gated by the stage-1 model, >= -6), max 3,000 chars         app/retrieval.py:526-611
  ▼
LLM (generate_answer: limit = 6 blocks) with "Source Book / Page / Chapter / Text"         app/generation.py:170, 296
  ▼
citations: the LLM returns [Book, Page N]; validate_generation() keeps only (book, page)
           pairs that were in the context and strips the rest from the answer text        app/generation.py:110-167
```

## 2. The thirteen questions

| # | Question | Answer from the code |
|---|---|---|
| 1 | Embedding model | `BAAI/bge-large-en-v1.5`, 1024-d, normalised. Query prefix `"Represent this sentence for searching relevant passages: "`. GPU: fp16; CPU (Docker, NAS): dynamic INT8 (`app/ingestion.py:160-177`). The code records the two agree to about 3 decimals. |
| 2 | Vector store / index | PostgreSQL + pgvector, column `chunks.embedding vector(1024)`, HNSW index `idx_chunks_child_embedding_hnsw` on child chunks, cosine distance, `ef_search = max(3*limit, 100)` per query. |
| 3 | Retrieval | `RetrievalService.search()` (`app/retrieval.py:640`): optional LLM rewrite → vector + keyword per query → RRF → rerank pool → two-stage cross-encoder → diversity → context expansion. |
| 4 | BM25 / keyword | Yes, but not BM25: Postgres full-text search (`to_tsvector('english')`, `ts_rank_cd`), strict `websearch_to_tsquery` first, then an OR query over significant words, abbreviation expansions, UK/US spellings and lost-ligature variants when fewer than 3 hits. |
| 5 | Reranker | Two stages. Stage 1 `cross-encoder/ms-marco-MiniLM-L-6-v2` orders the whole pool; stage 2 `ncbi/MedCPT-Cross-Encoder` (`RERANKER_SECOND_STAGE`, `app/config.py:72`) re-orders stage 1's top `RERANKER_SECOND_STAGE_TOP_N = 8`. Background jobs (twists, harder versions, hardening) pass `deep_rerank=False` and use stage 1 only. **MedCPT is therefore already in production — as a second stage, not on its own.** |
| 6 | Chunking | Docling layout parsing per 50-page slice. **Parents** = layout paragraphs/tables (199,684 rows); **children** = sub-splits of parents (224,152 rows) that carry the embedding and the FTS index. Retrieval matches children and reranks/returns parents. |
| 7 | Chunk metadata | `chunks.book_id, chapter, page_number, parent_id, extra_metadata (JSONB)`. Parent `extra_metadata`: `type: parent`, `headings` (Docling's nearest headings), `is_table`, `slice`. Child: `type: child`, `original_text`, `is_table`. `books.title`, `books.subject`. |
| 8 | Book / chapter / section / page | `chapter` is a path string: the PDF bookmark outline plus Docling's nearest heading, joined with `" > "`, e.g. `Section 2 Thorax > Appendix 2: … > Table A2.3: …`. For books without usable bookmarks it is only the nearest heading (`HORMONAL CONTROL OF TUBULAR REABSORPTION`). There is **no separate section column**; section is the tail of the path when the path has more than one level. Page is the printed PDF page index. |
| 9 | Citations | The LLM is asked for `[Book Title, Page N]`; `validate_generation()` and `_strip_unknown_refs()` keep a citation only if that exact (book title, page) pair was in the context blocks. |
| 10 | Where chunks reach the LLM | `generate_answer()` → `retrieval_service.search(limit=6)` → `_format_context()` (`app/generation.py:170`), one block per `ContextChunk` with Source Book, Page, Chapter, Text. |
| 11 | Environment | Backend: Python 3.14 in Docker (`docker/backend.Dockerfile`), CPU. PC dev venv `.venv-gpu`: Python 3.14.2, torch 2.14.0+cu130, sentence-transformers 5.6.0, transformers 5.13.1, numpy 2.5.2, pandas 3.0.6, psutil 7.2.2. Production host: ZimaOS NAS, 3.8 GB RAM, about 1.3 GB free with MedCPT loaded (a second MedCPT process is OOM-killed). |
| 12 | Cached models (PC) | `BAAI/bge-large-en-v1.5`, `cross-encoder/ms-marco-MiniLM-L-6-v2`, `ncbi/MedCPT-Cross-Encoder` (837 MB), `BAAI/bge-reranker-base` (1.1 GB). Not cached before this work: `BAAI/bge-reranker-v2-m3`. |
| 13 | GPU | PC: NVIDIA GeForce RTX 2060, 6 GB, CUDA 13.0 available to torch. Production (Docker on the NAS): CPU only. |

Other things worth knowing before reading benchmark numbers:

* **The rewrite is an LLM call** (`role="fast"`, temperature 0). It is not fully deterministic, so the
  benchmark records the rewrite once per question and replays it.
* **Two queries are scored, and the max is kept.** Every cross-encoder in production scores each candidate
  against the original question and the rewrite, and keeps the better score.
* **The reranker sees the focus window, not the whole parent.** `_focus_text()` takes 1,500 characters of
  the parent around the matched child. That is the "passage" the benchmark scores too.
* **Exam level changes the pool.** With a programme set, `search()` adds per-book probes and per-book pool
  heads. The benchmark measures the default (no level) path, which every background caller and every
  user without a saved programme takes.
* **What happens after ranking is not reranking.** `select_diverse()` and `expand_context()` decide what
  the LLM sees from the ranked list. The benchmark scores the ranked list itself; production's final
  context also depends on book diversity.
* There is an older, smaller harness, `backend/scripts/eval_rerankers.py` (12 questions, keyword-predicate
  gold, 24-candidate pool built from 30-row lists). Its predicate idea and its 12 questions are reused
  here; it is left in place.

## 3. Where the benchmark intercepts

The cleanest seam is the moment `search()` has a list of candidate parents and their focus texts and is
about to call `rerank_chunks()` (`app/retrieval.py:764`). Everything before it is retrieval; everything
from it on is ranking.

The benchmark does **not** change `search()`. It rebuilds the candidate set with the same production
primitives — `rewrite_query`, `_embed_query`, `vector_search`, `keyword_search`, `looks_like_index_page`,
`_focus_text` and the constants `CANDIDATE_LIMIT`, `PER_LIST_TOP` — and the same RRF formula, but keeps
the top **50** parents by RRF (plus every list head) instead of production's 16 (plus heads).

So that this copy cannot silently drift from production, `labs/evaluation/reranker/run.py verify-pool` runs
the real `retrieval_service.search()` with `rerank_chunks` wrapped (in memory, for that process only) to
record the candidates production hands to the cross-encoder, and checks that every one of them is in the
benchmark's pool. The recorded rewrite is replayed so both sides see the same queries.

## 4. What "current reranker" means in the benchmark

Exactly production's ranking on the wider pool: ms-marco-MiniLM-L-6-v2 orders all candidates (max over
queries), then MedCPT re-orders that order's top 8 (max over queries). The two halves are also
benchmarked on their own: stage 1 alone (what background jobs use) and MedCPT over the full pool.
