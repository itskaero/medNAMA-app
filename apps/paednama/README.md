# PaedNAMA

A Paediatrics app for CPSP IMM and FCPS-II, built on medNAMA. It's a separate app for now and will be merged into medNAMA once it works. The full plan is in [docs/PAEDS_APP_PLAN.md](../../docs/PAEDS_APP_PLAN.md).

## Run it (on the PC)

**The easy way:** start Docker Desktop, then double-click `Launch-PaedNAMA.bat` in the repo root. It opens the server and web windows, then http://localhost:3100.

**By hand:**

1. Start Docker. The dev database is `mednama-dev-db` on port 5434.
2. Start the server, from the repo root:

   ```
   .venv-gpu\Scripts\python.exe -m apps.paednama.server.main
   ```

   It serves medNAMA's API and PaedNAMA's `/api/paeds` on port 8780.
3. Start the web app:
   - The first time only, run `apps\paednama\scripts\link_modules.bat`.
   - Then, in `apps/paednama/web`, run `npm run dev` and open http://localhost:3100.

Sign in with a medNAMA account. If you're already signed in to medNAMA on this machine, you're signed in here too.

## What's here

- **`data/cpsp_paeds.json`:** the CPSP Paediatrics curriculum (2026) as data. It holds the exam components with page numbers, the syllabus for IMM and FCPS-II, the Mini-CEX topics and the DOPS procedures. To rebuild it from the PDF, run `python apps/paednama/scripts/build_registry.py`.
- **`server/`:** the `/api/paeds` routes (exams, syllabus, topic search, profile, books and their roles) and PaedNAMA's own `paeds_*` tables.
- **`web/`:** sign-in, setup, Home, The exam, Syllabus and Library.
- **`tests/`:** run `.venv-gpu\Scripts\python.exe -m pytest apps/paednama/tests -q`.

## Rules while it's separate

- **No edits to `backend/` or `frontend/`.** Anything PaedNAMA needs to change is copied here and marked "merge back".
- **Ingest books on the PC with the server stopped.** The ingestion workers need the RAM.
