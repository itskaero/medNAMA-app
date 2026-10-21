# medNAMA Seed Studio

An internal dashboard for bulk MCQ and revision-note generation from the PGET and FCPS Tables of
Specifications (TOS): what is planned, running, done, failed and left, from saved records.

## Run it

1. Start **Docker Desktop**. The dev database `mednama-dev-db` must be up on port 5434.
2. Start the Studio with **either**:
   - double-click `labs\seed_studio\run_seed_studio.cmd`; **or**
   - from the repo root: `.venv-gpu\Scripts\python.exe -m labs.seed_studio.server`
3. Open **http://127.0.0.1:8770**.
4. Sign in with a medNAMA **admin** account from the PC's database. Student accounts are refused.

It binds to this PC only (127.0.0.1). The root `.env` supplies:
- `DEEPSEEK_API_KEY`: questions and notes.
- `GEMINI_API_KEY`: reads the TOS pages.

Optional environment variables:

| Variable | Default | Effect |
|---|---|---|
| `SEED_STUDIO_PORT` | 8770 | Port to serve on |
| `SEED_STUDIO_WORKERS` | 2 | Batches processed at once |
| `SEED_STUDIO_PRICE_IN`, `SEED_STUDIO_PRICE_OUT` | unset | USD per 1M tokens, for cost estimates |
| `SEED_STUDIO_STALE_S` | 600 | Seconds without a heartbeat before a running batch counts as interrupted |

Tests: `.venv-gpu\Scripts\python.exe -m pytest labs/seed_studio/tests -q`

The database tests use a stand-in generator: no AI calls, and they clean up after themselves.

## How it fits medNAMA

It reuses the app's code, so there is one pipeline, not a copy:
- the database;
- retrieval and reranking;
- the embedding model;
- the duplicate and answer-support checks in `app/quiz_generation.py`;
- revision sheets from `app/revision.py`.

Its own tables are the `seed_*` tables, defined in `backend/app/seed_models.py` with Alembic migrations `f7a9b1c3d5e7` and `a1c3e5f7b9d2`.

### What happens to new questions

- Validated questions go into the normal `mcqs` table with status **pending**, which students are never served.
- **Review → Approve** publishes them.
- A job can instead publish them on insert.
- Every attempt is kept in `seed_items`, rejected and duplicate ones included, with the reason.

### Curated TOS files (the source of truth)

`data/pget_tos.json` and `data/fcps_part1_tos.json` hold the TOS as prepared by hand. When a document has one, its tree
is built from it (`tos_json.py`), and its parse method shows "curated JSON":

- **When:** automatically at start-up whenever a file is newer than the last import, or with **Rebuild from curated JSON** in the explorer.
- **Counts:** only the file's own `total_mcqs` and `mcq_count`. The FCPS file has none, so its targets are internal.
- **Identity:** nodes keep their ids when the heading path matches, so jobs and questions stay attached. Nodes that are gone and have no work attached are removed.
- **Page readings:** these stay as a reference ("open source page"), but never overwrite a curated tree.

### Pipeline

**1. TOS reading.** Each page is rendered and read by Gemini vision; the text is kept as well.

**2. Stitching.** Headings that continuation pages leave out are filled in from the pages before.

**3. Count checks.** Counts are transcribed, never computed. Where they don't add up, the item is flagged "ambiguous" with the arithmetic, and you can open the source page from the explorer.

**4. Targets.** Each target is labelled with its source:
- official: count × multiplier (5× by default, 10× expanded);
- derived: a share of a parent count;
- override: set by an operator (audited);
- internal: a base per topic, used where the TOS prints no count. The FCPS prospectuses print none.

**5. Jobs.**
- Preview → confirm → batches.
- Each batch is one topic at one difficulty, or one revision note.
- Workers claim batches with `SKIP LOCKED`.
- Each batch saves in one transaction, so an interruption leaves all or nothing.

**6. Controls.**
- **Pause:** running batches finish; no new ones start.
- **Cancel:** pending batches are dropped, and an in-flight result is discarded, never inserted.
- **Retry failed:** puts the job's failed batches back in the queue.
- **Restart recovery:** after the Studio stops, quiet `running` batches go back to pending.
- **Overlap guard:** a job can't start while another active job has work on the same topics.

## Known limitations

- **TOS reading is model-made.** The PGET booklet's column layout is hard to transcribe. Check flagged items (⚠) against their source page, and correct a count in the explorer. Corrections survive re-reading.
- **The Gemini key is on the free tier,** about 5 requests a minute per model. Reading waits and retries; pages that still fail are retried with **Read missing pages**.
- **Notes don't report token usage,** because `app/revision.py` doesn't return it. Question batches do.
- **Notes can repeat themselves.** Sub-topics with generic names, such as "Overview" under one topic, can produce similar notes. Review flags them.
- **Paper II topics aren't linked to the app's subject taxonomy yet.** Seeded questions are tagged by programme and TOS node, not `fcps_subject`/`fcps_topic`, so Custom exam's subject filter doesn't see them until tagged.
- **It runs on the PC against the dev database.** Moving approved questions to the NAS uses the existing transfer route and is not part of the Studio.
