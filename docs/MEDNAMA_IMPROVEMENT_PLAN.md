# medNAMA: reliability, grounding and FCPS readiness plan

One plan covering the problems reported so far, why each happens, and the fixes in order. Sections 1–6 are the original proposal. **Section 0 records what measurement on the real database actually showed (it corrects some of the original guesses), and section 7 records what has been implemented.**

**Problems reported**
1. "Fluid of choice in hypertrophic pyloric stenosis" returned *"not covered in the provided textbooks"*, although Bailey & Love is ingested.
2. Chat and AI-MCQ generation show **"Failed to fetch"**, **HTTP 500** or **"Failed to generate MCQs"**. In one case the MCQs *were* generated and saved while the screen showed the error.
3. CPSP/FCPS questions use current terms and concepts (for example "paradoxical aciduria" in HPS) that a textbook edition may not state. A "books only, otherwise refuse" design fails these questions.
4. MCQ generation is not aware of the database, so it can keep generating the same questions again.

**Ingested books:** Bailey & Love, Davidson, Dhingra ENT, Levinson Microbiology, Snell Anatomy, Katzung Pharmacology, Guyton & Hall Physiology, Robbins Pathology, Ramadas Pathology, plus First Aid 2024 and a partial Nelson Ed22 Vol 1 (722 chunks; its ingestion looks incomplete).

**Model:** DeepSeek `deepseek-v4-flash` through the OpenAI-compatible client.

---

## 0. What measurement showed (2026-09-24, local dev DB, 11 books, ~105k child chunks)

Measured with `scripts/run_diagnosis.py` (16 mixed-subject questions) and direct probes of `generate_answer`.

**The confidence gate was NOT the cause.** All 16 questions scored 0.62–0.78 and passed the 0.55 gate. The gate separated nothing, so it has been removed as a filter.

**Why "fluid of choice in HPS" was refused:**
- The answer is in Bailey & Love p.280 (chunk 162653): *"0.9% saline with 0.15% KCl in 5% glucose given at 6–7.5 mL/kg/h … As the chloride deficit is replaced, the kidneys correct the pH."* That is the same sentence the MCQ explanation quoted.
- Vector search ranked it **#7**, but the ms-marco re-ranker scored it **−5.9** against the exam wording, so it fell out of the top 5 and DeepSeek never saw it. DeepSeek then correctly said the passages it had didn't contain the answer.
- Rewriting the question into textbook terms lifts it to vector **#2**, re-rank **−0.56**.
- Parent "chunks" are single paragraphs (median **294 chars**). The paragraph that matched ("Pyloric stenosis presents with…") sits two paragraphs above the one holding the answer, so even a correct hit missed the answer.

**Why "paradoxical aciduria" was refused in chat:**
- Asked fresh, it *was* answered correctly (Davidson p.821, re-rank 7.17).
- The refusal came from asking it in a conversation after the HPS refusal: earlier "not covered" replies were replayed to the model as history.

**Why the MCQ explanation could mention it:** MCQ generation retrieved on the topic ("pyloric stenosis"), which matches the p.280 passage strongly. It had no refusal rule, and it never validated its sources.

**Other defects found:**

| Finding | Evidence |
|---|---|
| Bailey & Love lost its fi/fl/ff ligatures | ~2,600 passages; "fluid" is stored as "fuid" (×596), "first" as "frst" (×664). 612 damaged spellings in all. Keyword search for these words never matched. |
| Keyword search returned 0 hits for 7/16 questions | `websearch_to_tsquery` ANDs every word. The abbreviation expansion made it *stricter* (appended words were also ANDed). |
| No indexes on `chunks` | Every keyword query was a full scan computing `to_tsvector` on 105k rows: **2.3 s**. With a GIN index: **0.15 ms**. |
| `deepseek-v4-flash` reasons ("thinks") by default | A short call took 1.5 s and returned *empty* text, because reasoning used the whole token budget. With thinking disabled: 0.6 s. |
| `generate_ai_quiz` used an undefined `logger` | Any DeepSeek error in a batch became `NameError`, returned as "Internal Server Error: name 'logger' is not defined". |
| Next.js proxy timeout | Confirmed in `next/dist/server/lib/router-utils/proxy-request.js`: `proxyTimeout \|\| 30000`. |
| MCQ source attribution unverified | A generated fluid-regimen question was credited to First Aid p.387, which never mentions saline, dextrose or KCl. |

**So, is AI + RAG "too strict" or "wired wrong"? Both, in specific ways:**
- **Wired wrong:** exam wording wasn't translated into textbook wording, the context unit was too small, keyword search could only match all-or-nothing, and there were no indexes.
- **Too strict:** the prompt forced a refusal unless the passage stated the answer in the question's own words, and old refusals were replayed.
- MCQ generation had neither problem (and no checks at all), which is why the two features disagreed.

---

## 1. How the system works today

**Chat** (`backend/app/generation.py::generate_answer`)
1. **Confidence check:** `vector_search` + `keyword_search` → `calculate_confidence()`. **If the result is below 0.55, all retrieved passages are dropped** and the LLM gets "NO MEDICAL CONTEXT FOUND".
2. `hybrid_search()` runs vector and keyword search again on child chunks (about 150 words each). The results are merged with RRF (Reciprocal Rank Fusion) onto parent chunks. The ms-marco cross-encoder then re-ranks them, and the top 5 parents go to the LLM.
3. The prompt says: if the answer isn't in the context, return the **exact fallback sentence**.
4. `candidate_search()` runs retrieval and re-ranking a **third** time for the sources panel.
5. Any exception (API error, bad JSON, wrong model name) returns **the same fallback sentence**.

**AI MCQs** (`backend/app/main.py::generate_ai_quiz`)
- `hybrid_search(limit=6)` finds the context.
- The last 30 same-book MCQ stems are added to the prompt.
- DeepSeek is called **once per batch of 5, one batch after another**.
- After generation, `_is_near_duplicate` filters stems with difflib against those 30 stems.
- Everything is saved in one commit.

**Network path on the NAS:** Browser → Next.js (:3000, `rewrites()` generated in `docker/frontend.Dockerfile`) → FastAPI (:8000) → DeepSeek.

---

## 2. Root causes

### A. Refusals ("not covered")
| # | Cause | Where |
|---|---|---|
| A1 | `calculate_confidence` returns `max_vector_score` from every branch, so **keyword matches never count**. | `retrieval.py` |
| A2 | `websearch_to_tsquery` **ANDs every word** ("fluid & choice & hypertrophic & pyloric & stenosis"). Textbooks never say "fluid of choice", so keyword search returns 0 rows. | `retrieval.py::keyword_search` |
| A3 | The fixed 0.55 cut-off is applied to child embeddings that carry a `"Textbook \| Chapter \| Page"` prefix, which lowers similarity. Short exam-style questions score low. | `ingestion.py`, `ChatQueryRequest` |
| A4 | **British spelling** in Bailey & Love and Davidson ("hypochloraemic", "paediatric", "oesophagus", "haemorrhage") doesn't match American-spelled queries in Postgres FTS. | `retrieval.py` |
| A5 | The prompt only allows **"exact answer or refuse"**. The book says "correct the hypochloraemic alkalosis with 0.45% saline + 5% dextrose + KCl", not "the fluid of choice is…", so the model refuses. | `generation.py` prompt |
| A6 | Earlier refusals are replayed in `history`, which pushes the model toward refusing again. | `main.py::chat_query_endpoint` |
| A7 | Errors (API failure, bad JSON, wrong model name) look like "not covered". | `generation.py` `except` |
| A8 | The re-ranker is general web-search (`ms-marco-MiniLM`). The synonym map has only 10 entries (no HPS/IHPS, DKA, NS/RL…). | `retrieval.py` |
| A10 | **Chat and MCQ generation use different rules.** MCQ generation has no confidence check and no "refuse" rule, so it always receives the passages and the model can add its own knowledge. Chat drops the passages when the vector score is below 0.55 and then must refuse. The same Bailey & Love page 280 content (HPS → paradoxical aciduria) appears in an MCQ explanation but is refused in chat. MCQ explanations and sources are also not validated, so book facts and model knowledge mix without a label. | `main.py::generate_ai_quiz` vs `generation.py::generate_answer` |
| A9 | Sources aren't returned on a refusal, so the user can't see what nearly matched. | `generation.py` |

### B. "Failed to fetch" / 500 errors
| # | Cause | Evidence |
|---|---|---|
| B1 | **The Next.js rewrite proxy has a default `proxyTimeout` of 30 s.** A longer request is cut off, and the browser gets a dropped connection ("Failed to fetch") or a plain-text 500. **The backend keeps running and saves the result.** | Confirmed: pyloric stenosis MCQs were saved while the screen showed an error. |
| B2 | **MCQ batches run one after another.** 10 MCQs = 2 calls, 20 = 4 calls → 40–120 s or more. | `main.py` `for batch_start in range(...)` |
| B3 | **Chat repeats retrieval three times** and re-ranks on the CPU, then waits for DeepSeek (which may spend time reasoning). | `generation.py` |
| B4 | **`proxySafeFetch` retries on exactly these failures** and assumes the backend never received the request. In fact the backend is still working. The retry **starts a second full run**, which doubles DeepSeek cost, can save **duplicate quiz sets**, and times out again. | `frontend/src/lib/proxyFetch.ts` |
| B5 | **Real backend 500s in MCQs.** One batch with bad JSON or an API error raises `HTTPException(500)` and throws away the batches that already succeeded. | `main.py::generate_ai_quiz` |
| B6 | The DeepSeek client has no timeout. The SDK default is 600 s with 2 retries, so a slow upstream can hold a request for minutes. | all three `OpenAI(...)` calls |

The backend's `--timeout-keep-alive 70` already covers the idle-socket keep-alive case. What remains is caused by **request length**, not keep-alive.

### C. Books-only design versus CPSP reality
The books were added for **trust, MCQ content and level setting** (undergraduate, FCPS-I, FCPS-II/resident). They were not meant as a hard limit on what the AI may know. CPSP tests current terms and guidelines that the books may not state, so refusing whenever the books are silent fails the target users.

### D. MCQ repetition (not database-aware)
- Only the **last 30 MCQs from the same book** are shown to the LLM. When no book is chosen, it's the last 30 of any subject. Older questions on the same topic are invisible to it.
- The duplicate check is **text-only difflib > 0.8**. A reworded question testing the same fact ("HPS → hypochloraemic hypokalaemic metabolic alkalosis") passes as new.
- The **same query always retrieves the same top-6 chunks**, so the same facts get tested each time.
- Nothing tracks **which facts or chunks have already been tested**, or **which questions a user has already seen**.
- The B4 retry can also save a **full duplicate set**.

---

## 3. The target approach: books first, AI fills gaps, clearly labelled

1. **Chat answer in two labelled parts**
   - **From your textbooks**: book and page citations, checked on the server with `validate_generation` (existing).
   - **Additional clinical knowledge (AI, not from textbooks)**: current terms, guidelines and exam pearls. No citations are allowed here.
   - Refuse only for non-medical questions, or when the model is actually unsure.
   - JSON: `textbook_answer_markdown`, `supplementary_markdown`, `grounding` (`textbook` | `partial` | `ai_only`).
2. **Grounding badge:** "Textbook-backed", "Partly backed" or "AI knowledge only".
3. **Level setting** (`undergraduate` | `fcps1` | `fcps2`), stored on the user and passed to chat and MCQs. It controls depth and style. The books set the baseline.
4. **MCQs**
   - Facts and level come from the retrieved passages, and current CPSP concepts are allowed.
   - Each question is tagged `book_sourced` (with a validated book and page) or `ai_supplemented`, and never gets a made-up page.
   - If nothing is retrieved, generate the set as `ai_supplemented` with a notice, instead of the silent `Topic: …` fallback.
   - Add an **FCPS profile**: single best answer, 5 options (A–E), CPSP-style clinical scenarios, and subject/specialty tags.
5. **Safety:** server-side citation validation stays, citations in the supplementary part are stripped, and a "Report wrong answer" button feeds an admin review queue.

---

## 4. Implementation plan (phased)

### Phase 1: Stop the errors (small, low risk; do first)
1. **Proxy timeout:** in `docker/frontend.Dockerfile` **and** `NAS/frontend.Dockerfile`, add `experimental: { proxyTimeout: 180_000 }` to the generated `next.config.ts`, then rebuild the frontend.
2. **Safe retry:** `proxySafeFetch` retries only if the failure happened within about 2 s of sending (the real stale-socket case). It never retries after a long wait.
3. **Idempotency key:** the frontend sends an `Idempotency-Key` (UUID) with each chat or quiz request. The backend stores `request_id` on the quiz set or message. A repeated key returns the saved result instead of generating again. This removes duplicate sets.
4. **Recover a result after a timeout:** if the quiz request still fails on the client, re-fetch `/api/chat/ai-quizzes` (by idempotency key) and show the set if it was saved. The user then never sees an error for work that succeeded.
5. **DeepSeek client:** `OpenAI(timeout=60, max_retries=1)` in all three places, `max_tokens` set, and thinking/reasoning turned off or lowered for chat and MCQs if the model supports it.
6. **Partial-success MCQs:** retry a failed batch once, keep the batches that succeeded, return `{created, failed_batches}`, and return 504 with a JSON `detail` for a provider timeout.
7. **Backend logging:** app loggers (`app.generation`, `app.retrieval`) have no handler, so INFO lines such as "Retrieval confidence low" never appear in `docker logs`. Only uvicorn access lines (`POST /api/... 200`) and warnings/errors show. Fix: `logging.basicConfig(level=LOG_LEVEL, format=...)` in `main.py`, and log per request: query, confidence, gate result, top chunk IDs, DeepSeek latency and errors. `backend/scripts/diagnose_query.py` is a read-only diagnostic until then.
8. **One-command diagnosis script** (`backend/scripts/run_diagnosis.py`, added): works on the Windows PC and on the NAS. It auto-detects the running backend container (`mednama-backend` or any `*backend*` container, or `--container NAME`), copies `diagnose_query.py` into the container's `/tmp`, finds the image's backend directory, and runs each query. It saves the combined report to `diagnosis_<timestamp>.txt`. `--local` runs it without Docker against a reachable DB (for example the PC's `localhost:5433`). It is read-only: no DB writes, no rebuild or restart, no DeepSeek calls, so it doesn't interfere with the layered image or the database.
9. **Quiz UI:** give it its own `AbortController` (about 180 s) and a clear timeout message, matching `useChat.ts`.

### Phase 2: Make it fast
1. Run the MCQ batches **in parallel** (`ThreadPoolExecutor` or `AsyncOpenAI` + `gather`), then merge and deduplicate.
2. Chat: **one** retrieval pass. Compute confidence from `hybrid_search`'s re-ranked results, and build `sources` from the same list (drop the extra `candidate_search` and duplicate gate searches).
3. **Later:** SSE streaming for chat, and MCQ generation as a background job (`POST` returns `job_id`, then poll `GET /api/chat/ai-quizzes/jobs/{id}`), following the ingestion job pattern. After this, request length doesn't matter.

### Phase 3: Retrieval quality (fix the refusals)
**Scope:** these fixes are general. They change how *every* question is searched, not only HPS. HPS is just the example that exposed the problem. Refusals affect several question styles: short terms ("paradoxical aciduria"), exam phrasing ("X of choice", "most common", "investigation of choice"), abbreviations (DKA, MOA), eponyms, US vs UK spelling, and long clinical scenarios. `run_diagnosis.py` runs a 16-question mixed suite (all 9 books, all these styles) and prints the refusal rate. Run it before and after each phase to prove the improvement across subjects.

1. `calculate_confidence`: use the **cross-encoder score of the top re-ranked parent**, or a combined vector and keyword score. Pass borderline context through labelled "low confidence" instead of dropping it. Set the default threshold to about 0.45.
2. Keyword search: remove question stop-phrases ("of choice", "drug of", "management of", "treatment of"), and **fall back to an OR query** when the AND query returns 0 rows.
3. **US↔UK spelling expansion** (`-emia/-aemia`, `pedi/paedi`, `esoph/oesoph`, `hem/haem`, `edema/oedema`, `anemia/anaemia`).
4. Extend `MEDICAL_SYNONYMS` (HPS/IHPS, DKA, AKI, ARDS, NS/RL, PPH, MI, etc.).
5. **Query rewrite** for exam phrasing. A small LLM or rule-based step turns "fluid of choice in HPS" into "hypertrophic pyloric stenosis fluid resuscitation hypochloraemic metabolic alkalosis saline potassium". Retrieve with both queries and merge with RRF.
6. **Optional:** a medical-capable re-ranker (`BAAI/bge-reranker-v2-m3`), and re-embedding children without the metadata prefix.

### Phase 4: Books-first + labelled AI supplement (section 3)
1. `generation.py`:
   - Replace the "exact fallback" rule with the two-part answer.
   - Return a `grounding` level and a distinct `status` (`ok` | `low_confidence` | `llm_error`).
   - Always return `sources`.
   - Log the confidence, the top chunk IDs and the model error.
2. Don't replay canned fallback text in `history`.
3. User `level` field and selector. It's passed into both prompts.
4. Frontend: two-part answer rendering, grounding badge, and a "Report wrong answer" button.

### Phase 5: Database-aware MCQ generation (no repeats)
1. **MCQ embeddings:** add `mcqs.stem_embedding vector(1024)` (same bge-large model), filled when a question is created, plus a one-off backfill script for existing rows.
2. **Topic-aware "already asked" context:**
   - Before generating, embed the prompt and fetch the **top ~40 existing MCQs by vector similarity** (all books, not only the last 30 of one book).
   - Send each as a one-line summary of **what it tests** (stem + correct answer).
   - Instruct the model to test *different facts or angles*.
3. **Semantic duplicate check** after generation:
   - Reject a new question if cosine similarity to any existing stem is > 0.90.
   - Or if it has the same correct-answer text with similarity > 0.80.
   - Keep difflib as a quick first filter.
4. **Rotate the source passages:**
   - Record the source chunk IDs used on each MCQ (`mcqs.source_chunk_ids`).
   - When building context, **prefer passages not yet used** for this topic. Retrieve the top ~20, drop or down-weight chunks already used, and pick 6.
   - Coverage then spreads across the chapter instead of repeating the top result.
5. **Per-user freshness:**
   - Prefer questions the user hasn't attempted when building drills. `quiz_attempts` already exists, so it can drive a "seen" filter.
   - Offer "reuse existing bank questions first, generate only the shortfall". This saves DeepSeek cost and gives variety.
6. **Fact tags:** the LLM also returns `tested_concept` (for example "IHPS – metabolic derangement"). Store it on `mcqs`, and add a `(topic, tested_concept)` uniqueness check per book to block exact concept repeats.
7. The idempotency key from Phase 1 prevents whole-set duplicates caused by retries.

### Phase 6: Content and FCPS features
- **Books to add, by priority:**
  1. Paediatrics (Nelson Essentials or Ghai): the largest gap.
  2. Gynaecology/Obstetrics (Ten Teachers or Dutta).
  3. Biochemistry (Lippincott or Harper): the only missing FCPS-I basic science.
  4. A high-yield FCPS review or short-notes book (it uses "drug/fluid of choice" wording).
  5. Optional: Ophthalmology (Kanski or Khurana), Forensic and Community medicine.
- **Later:** ingest guideline summaries and past-paper topic lists, so "latest terms" become cited content instead of AI memory.
- **Features:** timed mock papers weighted by the FCPS syllabus, high-yield topic lists per chapter, wrong answers turned into flashcards, and MCQ explanations reusing `generate_mcq_explanation` (with citation validation).
- **Validate MCQ `source_book`/`source_page`** against the retrieved chunks (reuse `validate_generation`).

---

## 5. Files to change
| File | Phases |
|---|---|
| `docker/frontend.Dockerfile`, `NAS/frontend.Dockerfile` | 1 |
| `frontend/src/lib/proxyFetch.ts`, `frontend/src/hooks/useChat.ts`, `frontend/src/components/views/QuizView.tsx` | 1, 4 |
| `backend/app/main.py` (`generate_ai_quiz`, `_is_near_duplicate`, `chat_query_endpoint`, request models) | 1, 2, 4, 5 |
| `backend/app/generation.py` | 1, 2, 4 |
| `backend/app/retrieval.py` | 2, 3, 5 |
| `backend/app/models.py` + new alembic migration (`mcqs.stem_embedding`, `source_chunk_ids`, `tested_concept`, `grounding`, `request_id`; `users.level`; `answer_reports`) | 1, 4, 5 |
| `backend/scripts/` (MCQ embedding backfill, evaluation set) | 3, 5 |
| `backend/app/ingestion.py` (only if re-embedding) | 3 (optional) |

## 6. Verification
- **Errors:**
  - On the NAS, time `POST /api/chat/generate-ai-quiz` with count 20. Call `backend:8000` from inside the frontend container (the backend isn't exposed to the host), then call through `:3000`.
  - Before the fix, the call through `:3000` should fail at about 30 s. After it, it should succeed.
  - The backend log should show one run per request (no duplicate DeepSeek calls), and repeating an idempotency key should return the saved set.
- **Speed:** a 20-MCQ set takes about as long as one batch after Phase 2, and chat runs one retrieval pass per question.
- **Refusals:**
  - Build a set of about 30 exam-style questions ("X of choice", management, current terms) with the expected book and page, and run it with `backend/scripts/evaluate_rag.py` before and after.
  - Track hit@5, refusal rate and citation validity.
  - The HPS question should cite Bailey & Love (0.45% NaCl + 5% dextrose + KCl, correct the alkalosis before pyloromyotomy).
  - "Paradoxical aciduria" should give a textbook-cited part if present and a labelled AI part otherwise.
- **Repetition:**
  - Generate 3 sets of 10 on "pyloric stenosis".
  - Expect 0 pairs with stem cosine > 0.90, and more distinct `tested_concept` values and source chunk IDs across the sets than today.

---

## 7. Implementation status (2026-09-24)

| Plan item | Status | Where |
|---|---|---|
| Phase 0: `diagnose_query.py` `NameError` | Done | `scripts/diagnose_query.py` |
| 1.1 Proxy timeout 180 s | Done | `docker/frontend.Dockerfile`, `NAS/frontend.Dockerfile` |
| 1.2 Retry only fast (< 2.5 s) failures | Done | `frontend/src/lib/proxyFetch.ts` |
| 1.3 Idempotency key | Done: `request_id` becomes the quiz set id; a repeat returns, or waits for, the first run | `app/quiz_generation.py` |
| 1.4 Recover a set after a client timeout | Done: polls `GET /api/chat/ai-quizzes/{id}` | `QuizView.tsx`, `main.py` |
| 1.5 DeepSeek timeout/retries, thinking off | Done: shared client, 60 s, 1 retry; `LLM_THINKING=false` by default | `app/llm.py`, `config.py` |
| 1.6 Partial-success MCQs, JSON 502/504 | Done | `app/quiz_generation.py` |
| 1.7 Backend logging | Done: `LOG_LEVEL`; logs search context, rerank score, DeepSeek latency, grounding | `main.py` |
| Missing `logger` in `main.py` (real 500 source) | Fixed | `main.py` |
| 2.1 Parallel MCQ batches | Done (up to 4 at once, plus one top-up round) | `app/quiz_generation.py` |
| 2.2 One retrieval pass per chat question | Done (`search()` returns context + sources) | `app/retrieval.py`, `generation.py` |
| 2.3 Streaming chat | Done: `POST /api/chat/query/stream` (SSE). Progress events + a heartbeat every 3 s, so the connection is never idle; the UI shows the real stage | `main.py`, `useChat.ts` |
| 2.3 MCQ background jobs | Done: `POST /api/chat/generate-ai-quiz/jobs` returns at once; UI polls `GET …/jobs/{id}`. Idempotent on `request_id` | `quiz_generation.py`, `QuizView.tsx` |
| 3.1 Drop the vector-score gate | Done; unrelated passages are dropped by rerank score < −7 instead | `retrieval.py` |
| 3.2 Keyword OR fallback, strip exam phrasing | Done | `retrieval.py::keyword_search` |
| 3.3 UK/US + lost-ligature variants | Done at query time | `retrieval.py` |
| 3.4 More abbreviations | Done (~30) | `MEDICAL_SYNONYMS` |
| 3.5 LLM query rewrite | Done (≈1 s, cached, `QUERY_REWRITE=false` to disable) | `retrieval.py::rewrite_query` |
| New: neighbour-paragraph context expansion | Done (±3 paragraphs on the same page, ≤ 3,000 chars) | `retrieval.py::expand_context` |
| New: rerank on the matched part of long chunks; skip index pages | Done | `retrieval.py` |
| New: DB indexes (GIN full-text, parent_id, book/page) | Done (migration `b7d2e4f6a8c1`) | `alembic/versions/` |
| New: repair Bailey's ligatures in the data | Applied after a full backup (`medrag-pre-ligature-repair-20260924.dump`): 612 spellings, 8,108 child chunks re-embedded | `scripts/repair_ligatures.py` |
| 3.6 Medical re-ranker | Done, by measurement (`scripts/eval_rerankers.py`, 12 questions with known answer passages): ms-marco MRR 0.815 (hit@1 9/12); MedCPT alone 0.840 but ~5× slower; **two-stage ms-marco → MedCPT on the top 8: MRR 0.885 (hit@1 10/12)**, adopted. `RERANKER_SECOND_STAGE=""` disables it on a slow NAS | `retrieval.py`, `config.py` |
| 3.6 Re-embed children without the metadata prefix | Not done, deliberately: vector scores were not the bottleneck (all 16 test questions score 0.62–0.78), and re-embedding ~105k chunks on CPU takes hours | |
| 4.1 Two-part answer + `grounding` + `status` | Done; `answer_markdown` stays for existing screens | `generation.py` |
| 4.2 Don't replay refusals in history | Done | `generation.py` |
| 4.3 User level | Done: chat selector (General / MBBS / FCPS-I / FCPS-II), remembered per browser | `ChatView.tsx`, `page.tsx` |
| 4.4 Grounding badge | Done | `AIMessage.tsx` |
| 4.4 "Report wrong answer" | Done: Report on chat answers and MCQ explanations; admin review queue on the dashboard (resolve / dismiss / reopen) | `answer_reports` (migration `c9e3a5b7d2f4`), `ReportButton.tsx`, `ReportsPanel.tsx` |
| 5.1 MCQ stem embeddings (+ lazy backfill) | Done | `mcqs.stem_embedding` |
| 5.2 "Already asked" = 40 most similar existing MCQs | Done | `quiz_generation.py` |
| 5.3 Semantic duplicate check | Done (stem cosine > 0.90, or > 0.80 with the same answer) | |
| 5.4 Source-passage rotation | Done (`source_chunk_ids`, down-ranked on the next set) | |
| 5.5 Per-user "unseen first" drills | Done: practice quizzes put never-attempted questions first (`prefer_unseen`, default on) | `start_quiz_endpoint` |
| 5.6 `tested_concept` tags | Done | |
| New: verify MCQ source actually states the answer | Done (else labelled AI knowledge, no page) | `quiz_generation.py::_answer_supported` |
| FCPS profile (A–E) | Done, default; USMLE A–D selectable | `QuizView.tsx`, `PROFILES` |
| New: batch angles + concept-level de-dup | Done: parallel batches cover different angles; the same fact reworded is rejected (concept cosine > 0.88) | `quiz_generation.py` |
| 6: FCPS mock paper | Done: one-click preset, 50 questions / 60 min / all subjects / board mode / unseen first | `QuizView.tsx` |
| 6: Wrong answers → flashcards | Done: "Make flashcard" on missed questions in quiz review | `QuizView.tsx` |
| 6: Books to add | Your action: Paediatrics (finish Nelson ingestion), Gynae/Obs, Biochemistry, an FCPS review book | |

**Measured after the changes** (local, models already loaded):
- **Chat:** "fluid of choice in HPS" is answered and cites Bailey p.280 (0.9% saline + 0.15% KCl in 5% glucose). "Paradoxical aciduria" cites Bailey p.1190 and Davidson p.821 and p.385. Both are labelled "Partly textbook-backed". Answers take about 7–12 s.
- **Search suite:** 16/16 questions get textbook context. Keyword hits are ≥ 3 for every question (7 had 0 before).
- **MCQs:** 10 FCPS questions take 2 parallel batches of about 5–6 s each. 3 repeats of earlier HPS questions were rejected, the next set used different passages, and a retry with the same `request_id` returned in 0.0 s.

**Measured at the end (local Docker stack, through the Next.js proxy on :3000, CPU idle):**
- **Streamed chat:** first byte in 0.01–0.11 s, a heartbeat every 3 s, and a full answer in 9–18 s. About 5.5 s of that is DeepSeek (thinking off); the rest is search.
  - 8/8 test questions were answered, including one-line ones ("define shock", "what is anemia", "causes of jaundice", "DOC for absence seizure"), with citations.
  - "Fluid of choice in HPS" cites Bailey p.280, which is now the top context after the ligature repair and the two-stage re-ranker.
- **MCQ jobs:** the start request returns in 0.09 s. 10 FCPS questions were ready in ~25 s (9 with a cited book and page, 1 labelled AI knowledge). A repeated `request_id` returns the same job.
- **Retrieval suite:** 16/16 questions get textbook context.
- **Reports:** create, list, dismiss and reopen verified end to end.

---

## 8. Round 3 (2026-09-24): multi-book answers, real figures, buzzwords, model switch, one-command NAS deploy

**Findings**
- **"Pathology of angiofibroma" cited only Dhingra.** Bailey p.788 *was* retrieved, but:
  - Dhingra filled 3 of 4 context slots.
  - Neighbour expansion put unrelated paragraphs (septal perforation, HHT) in front of the angiofibroma sentence.
  - Nothing guaranteed each relevant book a slot, so a slightly different query rewrite dropped Bailey entirely.
- **Robbins/Ramadas are not missing sources.** Basic Robbins mentions angiofibroma only as the facial lesion of tuberous sclerosis; Ramadas has no angiofibroma passage.
- **Images failed to load.** `FiguresDrawer.tsx` had its own `API = "http://localhost:8000"`. The Docker build only rewrites `lib/constants.ts`, so thumbnails requested the unexposed backend port.
- **Figures were unusable even when they loaded.** All 11,043 had synthetic labels and no captions, ~2,700 were decorative (icons, QR codes), and they were attached by page alone, so the LLM guessed.

**Changes**
- Book-diverse context:
  - every book within 4.0 rerank points of the top passage gets a slot
  - at most 2 blocks per book
  - 6 blocks in total
- Relevance-gated neighbour paragraphs: the first-stage score must be ≥ −6, or the neighbour is a short adjacent caption/heading.
- The prompt tells the LLM to integrate and cite every contributing book.
- "Also covered in" lists relevant books the answer didn't cite.
- Figures:
  - The frontend URL bug is fixed, with an "unavailable" fallback.
  - `scripts/backfill_figure_captions.py` paired 5,264 figures with their printed captions (spot-checked visually) and flagged 2,731 as decorative.
  - Captions are embedded, and a figure is shown only if its caption matches the question (cosine ≥ 0.60; relevant 0.67–0.68 vs unrelated ≤ 0.52 in calibration).
  - Uncaptioned figures are never shown (user's choice).
  - Thumbnails and the lightbox show the caption plus book and page.
  - New ingestions capture Docling captions and skip decorative images.
- Buzzwords and mnemonics:
  - A collapsible section on chat answers, with "Save as flashcards".
  - A one-line **Buzzword** on generated MCQs.
  - AI-made mnemonics are labelled.
- `LLM_CHAT_*` / `LLM_FAST_*` settings let another OpenAI-compatible model (e.g. MedGemma) run answers or bulk work. `scripts/eval_answers.py` scores 30 FCPS-style questions (key-fact recall, books cited, latency) for a head-to-head.
- One-command deploy:
  - `scripts/make_nas_bundle.sh` (checks, build, save, checksums, manifest, optional MedCPT model).
  - `mednama-nas-deploy/deploy.sh` (checksum verify, DB backup, `:previous` rollback point, load, start, health wait, smoke test, `--data-fixes`, `--keep-env`) and `rollback.sh`.
  - A root `.dockerignore` stops sending ~2 GB of dumps as build context. `.gitattributes` keeps `*.sh` LF.

**Measured**
- The retrieval suite (now 21 questions) gets context for 21/21. The 5 multi-book questions use 2–4 books each.
- "Pathology of angiofibroma", 3 fresh runs: Bailey p.788 **and** Dhingra cited every time. Figures: Dhingra "Figure 49.1 Angiofibroma… H&E" and "Figure 49.2 specimen/CT".
- The HPS fluid question still cites Bailey p.280 and now attaches "Figure 17.9 Pyloromyotomy".

**Known issue (pre-existing, not fixed):** the first migration `f4faedc5e8bb_initial_schema` alters tables it doesn't create, so `alembic upgrade head` fails on a brand-new empty database. Existing databases (PC, NAS) are unaffected. Fresh installs must restore a dump first.

**NAS performance (found during the first NAS deploy):**
- On the NAS (4 cores, 3.8 GB RAM) answers took 31–48 s. DeepSeek accounted for only 4–7 s; the rest was retrieval.
- A 4-thread profile showed the main cost was vector search: **~19 s cold for two queries.** The query ordered by `1 - distance DESC`, which no index can serve, and there was no vector index anyway, so every question scanned ~400 MB of embeddings. The NAS has too little RAM to cache that.
- Fixes:
  - HNSW index `idx_chunks_child_embedding_hnsw` (migration `e5a7c9d1f3b5`, 812 MB). `deploy.sh` builds it CONCURRENTLY before switching images, so the site stays up and the migration is a no-op. Single-process, because Docker's 64 MB `/dev/shm` breaks parallel builds.
  - `vector_search` now orders by distance, with `hnsw.ef_search` scaled to the limit and iterative scans when filtering by book or chapter.
  - Result: vector search **19 s → 0.07–0.16 s**.
- The rerank pool shrank from 24 to 16 by fused rank, plus the top 3 of every individual result list. The top-3 guarantee was needed because Bailey's angiofibroma paragraph ranks #2 in one list but #19 after fusion. Neighbour paragraphs are scored in one batch with the rewritten query only.
- The 21-question suite is unchanged (21/21 with context; multi-book questions 2–4 books).

## 9. Round 4 (2026-09-25): the FCPS daily loop, Answer-Key Referee, duels

The goal of this round is a daily habit that improves retention, rather than a bigger question bank. Competitors (ReviseFCPS1, PrepFCPS, Part1PK, FcpsWorld) all advertise book-cited banks, mocks and spaced repetition. medNAMA's differentiator is *visible, verified* evidence: the real passage, page and figure, plus honesty when the books are silent or disagree.

**Retention core** (`app/retention.py`, migration `f2b4d6e8a0c1`)
- **Concept cards and re-tests.** Every wrong answer, and every right answer tapped "Guess", produces a **concept card**:
  - the concept in 2–3 lines
  - a textbook quote, verbatim-checked against the chunk, with book and page
  - a captioned figure when one matches
  - an optional mnemonic, left empty rather than forced
  - The card is built by a single background worker, so it never blocks answering.
- **Review schedule.** Cards are reviewed at 1 → 3 → 7 → 16 → 35 → 60 days.
  - **Each review uses a different MCQ on the same concept** (a variant generated in the background), so students learn the concept rather than the answer letter.
- **Confidence tap** (Sure / Unsure / Guess) on every answer. Readiness counts only sure-and-correct.
- **Daily Dose** (`/api/study/daily`):
  - due reviews
  - 5 new MCQs from the weakest subject
  - one "spot the diagnosis" figure MCQ
  - one pearl
  - A streak counter with freezes for duty days.
- **Readiness meter:** mastered concepts and predicted score vs the 75% CPSP line, plus an exam-date countdown and a paced daily target.
- **Explain it back:** the student writes the concept in their own words. The AI grades it against the textbook passage, lists the missed points and updates the schedule.
- **Mock format.** FCPS profile is A–E. The mock presets follow the CPSP format: 100 questions in 2 h, no negative marking.

**Answer-Key Referee** (`app/referee.py`, admin-only while private)
- Takes a recall question plus the published key. It returns:
  - the textbook-supported answer
  - quoted evidence with book and page
  - whether it agrees with the key
  - a verdict: `supported` / `contradicted` / `books_conflict` / `textbooks_silent`
- Every quote is string-verified against its passage. If no quote verifies, the verdict is downgraded to `textbooks_silent`, with the AI reasoning labelled as such.
- The recall book is never used as evidence for its own keys.

**Private recall source (Rafiullah 14th ed.)**
- OCR: `scripts/ocr_book.py` (Docling + EasyOCR, full-page, local, resumable).
- Extraction: `scripts/extract_recalls.py` produces `recall_items` (numbered headline recalls and their "also asked as" variants) and `recall_book` pearls.
- Batch judging: `scripts/referee_recalls.py` writes the disputed-keys CSV.
- Output lives in git-ignored `data/private/`. All rows are `visibility = 'admin'`.
- The Referee screen has three tabs: paste-and-check, the recall bank (filter by verdict, chapter or disputed), and a **high-yield map** of how often each system appears in the recall bank.

**Engagement**
- **Challenge a friend:** `/?duel=CODE` gives both players the same 10 MCQs. Answers are hidden until you've played, and results appear side by side.
- **Study a chapter:** with a chapter scoped in chat, you can get a cited high-yield summary, or 10 MCQs drawn only from that chapter (background job).

**Measured**
- Retention engine: 4 wrong answers produced 4 cards and 4 re-tests, each with a **different** MCQ id. The Daily Dose builds in 1.7 s.
- Explain-back scored a correct explanation 95 and a wrong one 0, listing each error.
- The end-to-end loop through the frontend proxy (daily → answer → card → explain-back → readiness → duel → referee) passes 11/11 checks.
- Referee spot checks (to be confirmed by a doctor):
  - "pericardial cavity" key contradicted (Snell)
  - "hyperexcitability" key contradicted (Guyton)
  - PAN–hepatitis B supported
  - SA-node books conflict
  - cardiac-plexus textbooks silent
- First 83 headline recalls judged: 59 supported, 18 textbooks silent, 3 contradicted, 3 books conflict.
- Recall bank so far (OCR pages 1–148): 1,744 recalls (415 headlines). Cardiovascular 44%, Gastroenterology 21%, Pulmonology 18%.

**Still to do**
- Finish OCR and extraction for the remaining pages. Referee the remaining headlines, then the variants.
- A doctor needs to spot-check 30 verdicts; the target is ≥ 90% agreement.
- Phase D:
  - FCPS Part 2 (SEQ marking, TOACS image stations, viva drill)
  - a weekly mock with a percentile
  - a VPS launch with free/paid limits
- Showing Rafiullah-derived content to students requires the author's permission.
