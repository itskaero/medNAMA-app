# medNAMA — Handoff: work completed through Phase 1

**Date:** 2026-10-12
**For:** next agent / "antigravity" picking up this repo.
**Source of truth for requirements:** `revision.md` (binding; Q1–Q30, §1–§27). This file only records what is **done and verified**. The full audit + plan lives at `C:\Users\alita\.opencode\plan\medNAMA-phase0-plan.md` (architecture map, feature inventory, dependency map, risk register R1–R11, test baseline, Phase 0/1 execution logs §11–§13).

**Rule that governs everything:** do not rebuild the app; preserve and extend. Small, reversible, tested changes. Never claim a feature works unless it was implemented **and** the tests were actually run. Keep inference API-based (no heavy local LLMs on the 3.8 GB NAS). Do not invent prices, quotas, official blueprints, or station counts. Clinical accuracy and assessment integrity stay central.

---

## 1. Environment — how to run things

**Dev DB (Docker):** container `mednama-dev-db` (`pgvector/pgvector:pg17`, **port 5434**, creds `medrag:medrag`, volume `mednama-dev-db-data`). Two databases:
- `medrag` — dev DB, **the one the app uses**. Alembic head `c8e0a2d4f6b9`. Content = NAS content (20 books / 369,991 chunks / 17,343 figures / 75,347 MCQs), 2 users (`admin`,`student`), 0 history.
- `medrag_nas` — untouched raw NAS restore (head `b3c5d7e9f1a2`), kept as the source of record.

`DATABASE_URL=postgresql://medrag:medrag@localhost:5434/medrag` (root `.env`; `find_env_file()` resolves it regardless of CWD).

**Python venv:** `.venv-gpu` (CPython 3.14.8 via uv; torch 2.14.0+cu130, CUDA available on RTX 2060). Use `.\.venv-gpu\Scripts\python.exe`. Set `$env:HF_HOME="C:\Users\alita\.cache\huggingface"` before any model load.

**Start the backend** (host, no Docker build needed; warms models ~70–130 s):
```powershell
cd "E:\Projects\Coding Projects\medNAMA\backend"
$env:PYTHONIOENCODING="utf-8"; $env:HF_HOME="C:\Users\alita\.cache\huggingface"
& "..\.venv-gpu\Scripts\python.exe" -u -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --timeout-keep-alive 70 *> "..\_local\backend.log"
```

**Run one e2e suite** (standalone scripts, **not pytest**; need the backend up):
```powershell
cd "E:\Projects\Coding Projects\medNAMA\backend"
$env:PYTHONIOENCODING="utf-8"; $env:MEDNAMA_BASE_URL="http://127.0.0.1:8000"
$py = "..\.venv-gpu\Scripts\python.exe"
$env:MEDNAMA_ADMIN_TOKEN  = (& $py -c "from app.auth import create_access_token; from datetime import timedelta; print(create_access_token({'sub':'admin'},  timedelta(hours=6)))" | Select-Object -Last 1).Trim()
$env:MEDNAMA_STUDENT_TOKEN= (& $py -c "from app.auth import create_access_token; from datetime import timedelta; print(create_access_token({'sub':'student'},timedelta(hours=6)))" | Select-Object -Last 1).Trim()
& $py "tests\e2e\e2e_account.py"        # swap in any e2e_<name>.py
```

**Run the grounding harness** (runs production `generate_answer`; first Q ~90 s model load, rest ~12–20 s):
```powershell
cd "E:\Projects\Coding Projects\medNAMA"
$env:HF_HOME="C:\Users\alita\.cache\huggingface"
.\.venv-gpu\Scripts\python.exe -m labs.evaluation.grounding.eval_grounding --limit 12
# coverage probe (retrieval only, no LLM): ... -m labs.evaluation.grounding.probe_coverage --questions labs/evaluation/grounding/uncovered_questions.json
```

**NAS:** `192.168.1.42` (ZimaOS v1.7.1, ZimaCube), SSH **publickey-only** (key `_local/nas_key`, helper `_local/nas_ssh.py`). App DB there is `medrag_20261012_220436` (was `medrag_202609282252`; check `POSTGRES_DB` in `/DATA/mednama/app/.env`). **3.8 GB RAM: never run memory-heavy batch scripts inside its containers** (see §9). The 2.0 GB pull dump is still at `/DATA/mednama/tmp/nas-medrag-pull.dump` and locally at `_local/dumps/nas-medrag-20261012.dump` (sha256 `c8b3518429d70ad4951f79eda5f3e7bf09b6857c304abbe9465a8d6cc927d781`).

**Gotchas:** `pwsh` is NOT installed (only Windows PowerShell 5.1 `powershell.exe`). The register endpoint is rate-limited 5/60 s per IP (in-memory); the account e2e suite retries on 429.

---

## 2. Completed — Phase 0 (audit + critical fixes + data)

- **Full audit** → risk register R1–R11, feature inventory, dependency map, prioritised plan (in the plan file, §1–§9).
- **R1** repaired the corrupted NAS bundle compose **and** added durable parse guards (`scripts/make_nas_bundle.sh`, local `mednama-nas-deploy/deploy.sh`).
- **R2** pinned stage-2 reranker to `BAAI/bge-reranker-base` everywhere (config default was already correct; aligned untracked root compose + MANIFEST example).
- **R7** JWT fails closed on a weak/default secret unless `ALLOW_WEAK_JWT_SECRET=1` (`backend/app/auth.py`, `backend/app/config.py`).
- **R8** global 500 handler returns generic `{"detail": "Internal Server Error"}` (`backend/app/main.py`).
- **R6 + seed-migration replay bug** confirmed (first migration assumes `create_all()`; the 5 seed migrations re-`ADD COLUMN`s that already exist). **Worked around** via `alembic upgrade <rev>` then `alembic stamp head`. **Deliberately NOT rewritten** — those 5 seed migrations are the user's untracked in-progress work.
- **Data:** pulled the NAS live DB and merged its **content** into dev `medrag` (minus users/history). All content counts match `medrag_nas` exactly. See plan §12.

---

## 3. Completed — Phase 1 (grounding & trust, §6/§8/§10)

### 3.1 Account data export + deletion (§10) — DONE, tested
- **New** `backend/app/account.py`: `delete_user_data(db, user_id)`, `export_user_data(db, user)`. Deletion clears every per-user table explicitly (most per-user FKs have **no `ON DELETE`** and would raise); `duels` is owned by `creator_id` (no `user_id` column) and is handled separately; invite codes kept with only `created_by` dropped. Shared library content is never removed.
- Endpoints: `GET /api/auth/export`, `DELETE /api/auth/account` (password-confirmed, clears cookie), `DELETE /api/admin/users/{user_id}` (admin can't self-delete). Wired in `backend/app/routers/auth.py` + `backend/app/routers/admin_users.py`.
- **New** e2e suite `backend/tests/e2e/e2e_account.py`. **All 15 e2e suites pass.**

### 3.2 Grounding evaluation harness (§6) — DONE, baseline measured
- **New** `labs/evaluation/grounding/eval_grounding.py` runs production `generate_answer` unchanged; reports **mechanical** metrics (grounding class, abstention, citation count, textbook-vs-supplement share, citation-in-context) plus a scaffold for **labelled** clinician metrics (`expected_grounding`/`should_abstain`/`citation_supported`, defaulting absent — the harness never invents them).
- **New** `labs/evaluation/grounding/probe_coverage.py` — retrieval-only coverage probe (objective "is this covered?" signal, no LLM).
- **New** `labs/evaluation/grounding/uncovered_questions.json` — 8 fixtures (4 likely-uncovered recent-guideline, 4 high-risk emergencies), all flagged "NOT clinician-reviewed."
- **Baseline (12 standard Qs):** citations-in-context **1.0**, grounding `partial` on all 12, abstention **0.0**, mean citations 5.75, mean supplement ratio **0.336**.
- **Uncovered/high-risk probe (8 Qs):** recent-guideline questions get **weak or spurious** retrieval matches (e.g. a 2026 adult-diabetes guideline matched a *pediatrics* passage); high-risk emergencies are well-covered. Abstention rate still **0.0**; supplement share rises to **0.389** on uncovered Qs. Answers are transparent (the textbook part explicitly says "the retrieved passages do not contain…", and the AI supplement is under a clearly-labelled heading).

### 3.3 Uncertainty / grounding-confidence signal (§6) — DONE, verified live
Evidence showed the real gap was a **missing machine-readable uncertainty flag**, not egregious over-confidence. Added an additive signal (no answer-content change):
- `backend/app/generation.py`: `_supplement_ratio()` + `_uncertainty_signal()` → returns `grounding_confidence` (`high|medium|low`) and `uncertainty_note` on every successful answer (lines ~258–285, 441–449). Thresholds (`SUPPLEMENT_HIGH=0.50`, `SUPPLEMENT_MEDIUM=0.33`, `RERANK_WEAK=-2.0`, `RERANK_OFF_TOPIC=-6.0`) are **provisional**, tuned to the harness baseline — tighten as the labelled review set grows.
- `frontend/src/types/index.ts` (lines 58–60) and `frontend/src/components/AIMessage.tsx` (lines 192–205): a caution badge under the GroundingBadge when `uncertainty_note` is set (amber for medium, red for high).
- **Verified live:** covered Q (metformin) → `confidence=low`, no note; uncovered Q (2026 ADA HbA1c) → `confidence=medium` with "Your textbooks only partly cover this; the AI supplement is not a textbook fact."

### 3.4 Confidence collection (§8, Q28) — reviewed, adequate
The plan's own criterion ("collect before reveal") is **already met**: the quiz taker (`frontend/src/components/views/QuizView.tsx` ~1912) and Daily Dose (`StudyQuestion.tsx`) render a `ConfidenceRow` (Sure/Unsure/Guess) while unanswered; the value is stored in `quizConfidence` and submitted with the answer; the answer endpoint persists `confidence` to `answer_events` (defaults `"sure"` when skipped). Timed mocks (board mode) are **not** disrupted by answer-revealing prompts (e2e_security confirms keys are withheld). Verified by code trace + `e2e_mastery` passing (its scoring depends on `CONFIDENCE_WEIGHT`). A dedicated live check exists at `backend/tests/e2e/_conf_check.py` (needs the backend running).
- **Known minor deviations (product decisions, not made unilaterally):** confidence is collected *before* the response rather than strictly "after every response," and it is optional (silent default `"sure"` — which is a positive certainty claim the student may not have made; changing the default to `"unsure"` would alter FSRS retention weighting, so it was left as-is).

---

## 4. Test status (2026-10-12)

- **Backend e2e: 15/15 suites pass** against the rebuilt dev DB (host backend on `:8000`). `e2e_mediverse` is **flaky** — its twist-generation LLM call occasionally produces 3 duplicates that fail the dedup check ("No twist passed the checks"); it passes on re-run and is **unrelated** to the changes above (twists use a different code path than `generate_answer`).
- **Seed Studio pytest:** 25 passed, 6 skipped (DB-dependent tests self-skip).
- **No frontend tests / no CI** exist (risk R9). Backend pytest now exists for TOS: `backend/tests/test_tos.py` (see §9).
- Seed Studio pytest: 31/31 pass (2026-10-15). `test_overlap_pause_resume` used to fail. It was a real bug, not flakiness: see §10.

---

## 5. Git state — important

The working tree is **heavily modified and untracked**; only `docker/*` and `scripts/*` are tracked. Compose files, `NAS/`, `mednama-nas-deploy/` are intentionally gitignored. Branch `claude/medical-rag-coverage-gaps-12n5us` holds everything through §11 (committed and pushed 2026-10-21). PyInstaller `app-template/build|dist` and `_local/` stay ignored. Use `git -c safe.directory="E:/Projects/Coding Projects/medNAMA" …`. **Do not rewrite the 5 untracked seed migrations** (`f7a9b1c3d5e7`, `a1c3e5f7b9d2`, `c8e0a2d4f6b9`, `d5e7f9a1b3c4`, `e6f8a0b2c4d5`) — they are the user's in-progress work.

---

## 6. What's next (updated 2026-10-15)

Phase 2 (TOACS foundation) and much of Phase 3 (Station Studio authoring, versioning, review, challenges, calibration) are **done**; see §9 and §10. Remaining, roughly in order:

1. **Clinician review of the 6 published Paediatrics stations.** All six were **published as "Practice only" on 2026-10-15 at the owner's request**, without clinician review (approved_by says so), on both databases (PC #33, #115–#119; NAS #1–#6).
   - Before publishing, #117 and #119 got a v2 that drops their one unverified criterion, and #119 now releases the CSF results before the question that asks about them.
   - A reviewer should still trim #33 (15 criteria) and check #117 against Pakistan's EPI schedule, which Nelson doesn't cover.
   - Edits are new versions in Seed Studio → TOACS stations; then run `python scripts/nas_sync_stations.py`.
2. **Gold answers and calibration for each station**, so stations can move from "Practice only" to "Marking validated". This needs an expert to mark 3 sample answers per station in Station Studio.
3. **NTS bank:** not mapped to TOS, by the user's decision (2026-10-15): NTS is a different exam with no TOS topics. Paediatrics TOS topics stay without practice questions until Paediatrics MCQs are seeded, e.g. a Seed Studio question job on the PGET Paper II Paediatrics document.
4. ~~TOS sheets for all papers~~ **DONE 2026-10-16:** all 56 papers pre-written on the PC (1,275 new, 0 failed; 76 topics have no textbook passages and get no sheet, by design) and copied to the NAS (1,410 sheets), where they open instantly.
   - Lessons from the run: set `HF_HUB_OFFLINE=1` for batch jobs (the model library's internet check hung model loading), and load models before starting worker threads.
   - The sheet search query is now focused (`tos_sheets.search_query`). The old one, the topic plus 6 subtopics, made Postgres's keyword-search fallback rank nearly every chunk; queries took up to 3.5 minutes. The same fix applies to station drafting.
5. **TOS tag review:** 159k close calls are waiting in Admin → Content → TOS tag review (optional; they are held out of practice until reviewed).
6. **Later phases** (revision.md §26): voice viva, image and investigation stations, AI station variants, recollection bank, and stations for the newly enabled specialties (they need drafting and review).
7. **Phase 1 loose end:** a labelled, clinician-reviewed question set for the grounding harness (citation relevance, abstention correctness).

---

## 7. Blocked / needs a product decision

- **Clinician review:** stations (above), and any "assessment-ready" claim, need a qualified reviewer.
- **Commercial entitlements** (§10): no entitlement, billing or plan tables; `revision.md` forbids inventing quotas or prices. Needs an explicit product decision.
- **Voice viva** (§19/Q3): no speech-to-text or text-to-speech in the stack; text first.
- **Verified media stations** (§11/Q7): `figures`/`mcq_media` exist, but licensing must be verified.
- **Labelled eval metrics:** blocked on clinician review of a question set.

---

## 9. TOS-based preparation (2026-10-14) — DONE, deployed to the NAS

Spec: `TOS-BASED-EXAM-PREPARATION-FEATURE.MD`. Screen: Practise → TOS prep (`frontend/src/components/views/TOSPrepView.tsx`).

- **Topic mapping** (`backend/app/tos_tags.py`, `backend/scripts/tag_tos.py`): every question is tagged with its TOS topic per paper in `mcq_tags`:
  - `tos_topic`, `tos_subtopic`;
  - `tos_review` = close call, kept out of practice;
  - `tos_manual` = an admin's decision, never deleted by a re-tag.
  - Seed Studio questions use their `seed_node`. Others get the nearest topic by stem embedding; specialty papers use the specialty's pool across all of the paper's topics.
  - Calibrated on the 792 seed questions: ~93% right at the 0.01 margin.
  - Dev and NAS each have ~580k topic tags and ~159k close calls. 84 topics have no questions: Paediatrics, Community Medicine P2, and the MBBS clinical years — the bank has none.
- **Re-tagging the NAS:** `python scripts/nas_tag_tos.py --save-vectors-first` from the PC (SSH tunnel to the NAS Postgres). Running the tagger **inside** the NAS container hung the NAS for an hour (needed a power cycle). See `NAS/DEPLOY.md` §6.
- **Close-call review:** Admin → Content → TOS tag review (`GET/POST /api/tos/review`).
- **Fixed on the way:**
  - The refresher and notes called a non-existent `retrieval_service.retrieve` and always served template filler.
  - TOS answers were never saved: the submit used non-existent `QuizAttempt` fields. Answers now go through `retention.record_answer(source="tos")`.
  - LLM endpoints were unauthenticated.
  - "Write more" bypassed the generation pipeline; it now uses `generate_quiz_set` and tags the results.
- **Mocks:** built to the blueprint (official vs internal allocation named; FCPS has no printed counts, so it's never called compliant) and saved as personal `weekly_mocks` papers, sat through `/api/mocks/{id}/*`.
- **Image:** `docker/backend.Dockerfile` now bakes `data/*_tos.json` into `/app/data`. Before, they lived only in a hand-copied container layer.
- **Tests:** `backend/tests/test_tos.py` (32 pass) and `backend/tests/e2e/e2e_tos.py` (in-process, throwaway user, all pass). The previous unittest `e2e_tos.py` was overwritten; its still-valid checks moved into `test_tos.py`.
- **Seed Studio:** the job wizard accepts any bank-target multiplier from 0.5× to 20×.
- **Refreshers and notes** (`app/tos_sheets.py`): one generator and one set of cache keys, used by the app (written on first open) and by Seed Studio → **TOS sheets** (pre-writes a paper's missing sheets on the PC).
  - Copy them to the NAS with `python scripts/nas_sync_tos_sheets.py`, which goes through the SSH tunnel in `scripts/nas_tunnel.py`.
  - Retrieval for sheets skips the query rewrite and the second reranker: a first-time sheet on the NAS went from ~73–126 s to ~42 s; pre-written ones open in 0.1 s.
  - PGET Paper I is fully pre-written and synced (2026-10-14). Other papers are still written on first open.
- **Stale tabs:** after a deploy, a browser tab opened earlier keeps the old code and can show "[object Object]" or empty sheets until reloaded.

- **TOS screen layout:** a three-step path (1 Learn a topic → 2 Practise it → 3 Test yourself) with one "next step". Mocks sit after the topics as step 3, and practice answer modes are named "As I go" / "At the end".

## 10. TOACS / viva stations (2026-10-15) — foundation DONE, Paediatrics pilot, 6 stations published as practice-only

Spec: `revision.md` Parts III–IV. Plan: `C:\Users\alita\.claude\plans\other-phases-read-handover-md-merry-peacock.md`.

- **Data** (`backend/app/station_models.py`, migration `d9f1b3c5e7a0`, already applied on the NAS):
  - stations → immutable versions → rubric criteria;
  - attempts (pinned to a version) → per-criterion scores;
  - challenges (the original score is never edited);
  - expert gold answers, calibration runs, and the `station_audit` log.
  - Included in account export and deletion, and in `reset_user_data.py`.
- **Engine** (`backend/app/stations.py`):
  - **Drafting:** grounded in the specialty's books (Paediatrics = Nelson). Every criterion must quote its passage; unverified ones are flagged and block approval.
  - **Examiner:** asks the reviewed questions and follow-ups verbatim; the AI only picks follow-up vs next. Guided mode teaches, but only about the current question; a hint re-asks the question and caps later credit (`prompted_credit`). Timed mode uses fixed lead-ins, the server clock and auto-submit, and keeps going if the AI is down.
  - **Scoring:** every mark needs a quote of the candidate's words, checked by the server. An unverified quote leaves the mark flagged and uncertain. An unsafe finding without a quote is never applied. Pass/fail is given only if the version sets a pass mark and nothing is flagged.
  - **Calibration:** mark error, agreement, safety misses and false flags against expert-marked gold answers, using leave-one-out few-shot examples. A station is `assessment_ready` only after a pass; until then learners see "Practice only".
- **API** (`backend/app/routers/stations.py`):
  - learner routes `/api/stations*` and `/api/station-attempts*`;
  - admin routes `/api/admin/stations*` and `/api/admin/station-challenges*`.
- **App UI:**
  - Practise → **TOACS / viva**: library (`ToacsView.tsx`), runner and debrief (`StationRunner.tsx`).
  - Admin → Content → stations panel (`StationsAdminPanel.tsx`): publish, unpublish, retire, challenges.
- **Seed Studio → TOACS stations** (`labs/seed_studio/stations_studio.py`): draft per topic, edit (each save is a new version), approve, publish, write 3 sample answers, expert marking, run calibration. Drafting and calibration run on the PC only.
- **Tests:** `backend/tests/test_stations.py`, 19 tests with a scripted fake AI. Also checked end to end in a browser with the real AI on a temporary [TEST] station, since deleted.
- **Content status (superseded by §6):** one real AI draft (#33, "Neonatal Sepsis and Jaundice", 15 criteria, all citations verified) is on the **PC dev DB only**, as an unreviewed draft. It was drafted before the prompt was limited to 6–10 criteria. Nothing is published. **A clinician must review and approve stations before learners see them.**
- **Seed Studio bug fixed:** the "same topics" job-overlap guard never fired (raw SQL saw no unflushed batches), so two jobs could seed the same topics at once. `engine.create_job` now flushes first; `test_overlap_pause_resume` passes (it was not flaky).
- **Moving stations to the NAS:** stations are authored on the PC; `python scripts/nas_sync_stations.py [--ids …] [--dry-run]` copies stations, new versions, criteria, gold answers, the latest calibration and status through the SSH tunnel. Re-runs only add what is new; learners' attempts are never touched.
- **Seed Studio launchers (2026-10-15):** the compiled `app-template/dist/*/*.exe` looked for the repo relative to their own file. Inside the exe that pointed into `dist/`, so they could not find `.venv-gpu` and re-ran themselves instead of starting the app. They now search upwards for the repo; rebuilt with `app-template/build_all_apps.py`. Studio's page is now served with `Cache-Control: no-cache`, so browsers stop showing an old copy (one cause of still seeing only "5× / 10×"). The job wizard has offered presets plus any value from 0.5× to 20× since 2026-10-14.
- **Circuits (2026-10-15):** "Start a timed circuit" in the library sits 2–6 published stations of one specialty back to back (one per TOS topic where possible, least recently sat first), each with its own clock, then shows a combined result.
  - Stored as a `study_sessions` row (`kind='toacs_circuit'`), so no migration.
  - API: `POST /api/station-circuits`, `GET /api/station-circuits/{id}`, `POST /api/station-circuits/{id}/next`.
  - Labelled provisional, because there is no official station count or circuit blueprint.
- **Suggested level:** `stations.learner_level()` turns the learner's last 5 marked stations into a suggested difficulty, shown with its reason. Stations near it that are unsat or due a repeat get a "Suggested" badge. It is advice only: no station or timed attempt is changed.
- **Specialties in Station Studio:** Paediatrics (Nelson), Medicine (Davidson), O&G (Ten Teachers), ENT (Dhingra), Ophthalmology (Jatoi) and Surgery groups 1 and 2 (Bailey & Love), all on PGET Paper II blueprints. Pathology, Radiology, Psychiatry, Anaesthesia and dentistry have no textbook in the library yet. Stations exist only for Paediatrics so far.
- **Not built yet** (revision.md later phases): voice, images and investigations, AI station variants, recollection bank.

## 11. MVP focus: onboarding, four core hubs, Today plan, usage counts (2026-10-20)

The app had grown to ~25 screens. The user chose to keep everything but focus the path:

- **Navigation** (`frontend/src/lib/nav.ts`): Today · Study (My paper = TOS, Revise, Dr MedNama) · Practise (Programme exams, Custom exam, Past papers, TOACS) · Review (Mistakes, Notes & cards, Progress) · **More** (Check an answer, High-yield map, Look-alikes, Bookmarks, Question bank, Challenge, Offline, Library) · Admin. TOACS shows only to learners on a clinical paper (`Tab.clinical`, `visibleTabs`); every view still opens by URL.
- **Onboarding** (`components/PrepSetupDialog.tsx`): 3 steps (exam + paper, exam date, minutes/day). Shown only when missing: new users get the full setup; users who already chose an exam level only confirm their paper. "Later" waits until the next browser session. Reopened from the sidebar ("Paper: …").
- **Backend** (`app/routers/prep.py`, migration `e0a2c4e6f8b1`): `users.prep_profile` JSONB; `GET/PUT /api/prep/profile` (sets `exam_level` too, so Chat/Revise/MCQs follow); `GET /api/today/plan` (continue → ≥5 due reviews → today's TOS topic → mock when ≥50% practised or ≤14 days left → setup/Dose, with the weekly topic target); `POST /api/usage` and `GET /api/admin/usage`.
- **Today** shows the plan card (`TodayView` `PlanCard`); topic chips deep-link to `#/tos?exam=&paper=&topic=` (and `section=test` for the mock).
- **Usage counts** (`usage_counts(view, day, n)`, no user column) are in Admin → Content → Screen usage. **Decide what to promote, keep under More, or retire from these numbers after a few weeks, before building new features.**
- Today suggests only **studyable** topics (`prep._studyable`): a cached refresher/notes sheet or tagged bank questions. Paediatrics' top-weighted topic had neither and sent learners to an empty page.
- Tests: `backend/tests/test_prep.py` (7). UI walked through with Playwright (onboarding → plan → deep link → TOACS tab → phone width → admin usage).
- **Product page** (`site/`): Plan, Blueprint and Viva feature tabs (screens in `site/assets/shots/plan|blueprint|viva|onboarding.jpg`), a "Start with your paper" card, FAQ on viva practice; `progress.html` timeline adds blueprint, viva pilot and this release. Publish: `git subtree split --prefix site -b site-main && git push site site-main:main`.
- Deployed to the NAS 2026-10-21 after a DB backup (`/DATA/mednama/backups/medrag-before-prep-20261020-2108.dump`, 2.1 GB).

---

## 12. PaedNAMA: a separate Paediatrics app (planned 2026-10-21)

**What it is:** a sister app for CPSP Paediatrics IMM and FCPS-II, built in `apps/paednama/`. It imports medNAMA's backend as a library, uses the same database and accounts, keeps its own `paeds_*` tables, and **changes nothing in medNAMA** until it's merged later.

- **Full plan:** `docs/PAEDS_APP_PLAN.md`
- **Exam facts:** from the CPSP 2026 Paediatrics curriculum, `pdfs/paeds/prospectus.pdf`
- **Books:** all 12 Paediatrics books in `pdfs/paeds/` were ingested on the PC on 2026-10-21 (book ids 99–110, plus Nelson 22 at ids 88 and 89). They are **not yet copied to the NAS**.
- **How the ingestion was run:**
  - Book by book, without `HF_HUB_OFFLINE`: Docling downloads its layout models the first time.
  - Never alongside the PaedNAMA server or a Next dev server: on 16 GB, out-of-memory kills the workers ("process pool terminated abruptly").
  - Essential Clinical Skills crashed Docling on one page range at 32-page slices. It went through with `--slice-size 4`.

---

## 8. Binding constraints carried forward (from `revision.md`)

- Q1–Q30 are binding; clinical accuracy and assessment integrity are central.
- Keep inference API-based; do not add heavy local LLMs (NAS has 3.8 GB RAM).
- Evaluate retrieval/grounding changes with the harness rather than assuming.
- Do not present an unsupported inference as a textbook fact; express uncertainty when evidence is insufficient; stronger safeguards for high-risk clinical claims.
- Do not disrupt timed mock examinations with answer-revealing prompts.
- Privacy-by-default; data export and deletion (§10) — now implemented.
- Do not invent prices, quotas, official blueprints, or station counts.
