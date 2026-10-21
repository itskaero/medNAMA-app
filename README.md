# medNAMA

medNAMA is a study platform for FCPS, PGET and MBBS candidates, grounded in the textbooks they actually study. Answers, revision sheets and MCQs come from retrieval over 25 ingested medical textbooks, with page-level citations that are checked against the retrieved pages. Around that sit the following:

- **Practice:** a question bank, a past-paper archive, mocks and duels.
- **Retention:** Daily Dose, FSRS flashcards and mistake review.
- **Content tools:** an answer-key referee and seeding from Tables of Specifications.

The public product page lives in [`site/`](site/) and is published to **itskaero/medNAMA** (GitHub Pages). This repository, `medNAMA-app`, is private.

## Map of the repo

```
backend/            FastAPI app (Python): retrieval, generation, practice, retention, auth
  app/              the application: one module per concern (see "Where things live")
    routers/        HTTP endpoints, one file per area
  alembic/          database migrations (run on the dev DB: docker exec ... alembic upgrade head)
  scripts/          one-off and batch jobs: ingestion, seeding, tagging, transfers to the NAS
  tests/            end-to-end checks (tests/e2e)
frontend/           Next.js app (TypeScript): src/components/views has one file per screen
site/               the public product page (static HTML), published to itskaero/medNAMA
labs/               internal tools beside the app (not shipped to students)
  rag_lab/          RAG playground: compare retrieval/rerank/generation set-ups side by side
  seed_studio/      Seed Studio: TOS-driven bulk MCQ + revision-note seeding with a live dashboard
  evaluation/       reranker benchmark (questions, gold predicates, results)
docs/               plans, analyses and runbooks (index below)
docker/             Dockerfiles for the local stack
NAS/                production compose + Dockerfiles for the ZimaOS NAS        (local only)
mednama-nas-deploy/ deploy bundle: deploy.sh, rollback.sh, restore_nas.sh       (local only)
scripts/            local_up.sh (local stack), make_nas_bundle.sh (NAS bundle)
data/               TOS PDFs, taxonomy caches, transfer files                   (local only)
pdfs/  mcqs/        source textbooks and MCQ banks                              (local only)
logs/               ingestion and job logs                                      (local only)
_local/             old dumps, duplicate PDFs, scratch: safe to ignore          (local only)
```

"Local only" folders are gitignored: they hold data, credentials or machine-specific files.

## Where things live (backend/app)

| Area | Modules |
|---|---|
| Retrieval and answers | `retrieval.py` (hybrid vector + full-text, RRF, rerankers), `generation.py` (chat answers, MCQ explanations, citation validation), `llm.py` |
| Exam programmes | `exam_levels.py` (MBBS, FCPS-I/II, PGET, MD, MS, USMLE: guidance, intent, preferred books) |
| Revision | `revision.py` (one-page sheets), `rapid_review.py` |
| MCQs | `quiz_generation.py` (grounded AI MCQs), `hardening.py` (harder versions), `twists.py`, `checked_mcqs.py`, `photo_mcq.py` |
| Practice | `practice_scope.py` (Paper 1/2 scopes), `past_papers.py`, `answer_stats.py` (difficulty from answers), `study_plan.py` (yield-weighted topics) |
| Retention | `retention.py` (Daily Dose, concepts), `fsrs.py`, `study_modes.py`, `stats.py` |
| Answer keys | `referee.py` (textbook verdicts on recall keys), `textbook_refs.py` |
| Books | `ingestion.py`, `reingest.py`, `book_meta.py`, `book_pages.py`, `book_structure.py`, `taxonomy.py` |
| Data | `models.py` (app tables), `seed_models.py` (Seed Studio tables), `database.py`, `config.py`, `auth.py` |

## Run it

| What | How |
|---|---|
| Local stack (app on :3000) | `bash scripts/local_up.sh`, which passes the root `.env` (AI keys) |
| Seed Studio (:8770) | double-click `labs\seed_studio\run_seed_studio.cmd`, or `.venv-gpu\Scripts\python.exe -m labs.seed_studio.server` |
| RAG Lab (:8765) | `.venv-gpu\Scripts\python.exe -m labs.rag_lab.server` |
| Reranker benchmark | `.venv-gpu\Scripts\python.exe labs\evaluation\reranker\run.py --help` |
| Seed Studio tests | `.venv-gpu\Scripts\python.exe -m pytest labs/seed_studio/tests -q` |
| NAS deploy | `bash scripts/make_nas_bundle.sh`, copy, then `deploy.sh --keep-env` on the NAS (see `NAS/DEPLOY.md`) |
| Publish the product page | `git subtree split --prefix site -b site-main && git push site site-main:main` |

The dev database is the `mednama-dev-db` container on port 5434. Docker Desktop must be running for anything above.

## Docs

| Document | What it is |
|---|---|
| [docs/MEDNAMA_IMPROVEMENT_PLAN.md](docs/MEDNAMA_IMPROVEMENT_PLAN.md) | The improvement plan |
| [docs/competitor_gap_analysis.md](docs/competitor_gap_analysis.md) | AMBOSS, Osmosis, PrepFCPS, Medmacs, NEETPGAI and others against medNAMA, with recommendations |
| [docs/reranker_benchmark_architecture.md](docs/reranker_benchmark_architecture.md) | How the reranker benchmark is built |
| [docs/DB_TRANSFER_TO_NAS.md](docs/DB_TRANSFER_TO_NAS.md) | Moving the database to the NAS |
| [docs/analysis.md](docs/analysis.md) | Earlier analysis notes |
| [labs/seed_studio/README.md](labs/seed_studio/README.md) | Seed Studio: run, design, limitations |
| [labs/rag_lab/README.md](labs/rag_lab/README.md) | RAG Lab: configurations and reports |
| [labs/evaluation/reranker/RECOMMENDATION.md](labs/evaluation/reranker/RECOMMENDATION.md) | Reranker benchmark result |
