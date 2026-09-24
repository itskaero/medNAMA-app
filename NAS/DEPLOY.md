# Deploying medNAMA on the NAS

## Recommended: one-command image bundle (no build on the NAS)

Build on the PC, copy one folder, run one script on the NAS.

**On the PC** (Docker Desktop running, repo root, `.env` present):

```bash
bash scripts/make_nas_bundle.sh --with-models
```

This runs the checks (Python compile + TypeScript) and builds both images. It then fills `mednama-nas-deploy/` with:
- `mednama-backend.tar` and `mednama-frontend.tar`
- `SHA256SUMS` and `MANIFEST.txt` (commit, build date, schema version)
- `hf-medcpt.tar`: the MedCPT reranker, so the NAS needs no internet for it
- `deploy.sh`, `rollback.sh`, `restore_nas.sh`

**Copy** the whole `mednama-nas-deploy/` folder to the NAS (SMB share, `scp -r` or `rsync -avP`).

**On the NAS** (as root, inside the copied folder):

```bash
bash deploy.sh --data-fixes      # first deploy after the 2026-09 update
bash deploy.sh                   # every later update
```

`deploy.sh` runs these steps:
1. Verifies the checksums.
2. Backs up the database to `/DATA/mednama/backups/` and aborts if the backup is invalid. The new backend applies migrations when it starts.
3. Keeps the running images as `:previous`.
4. Loads and tags the new images.
5. Starts the stack under the existing compose project.
6. Waits until the backend is healthy.
7. Smoke-tests the page, the API and the figure route through the proxy on :3000.

Options:
- `--data-fixes`: repairs Bailey & Love's lost fi/fl/ff letters (re-embeds those passages, which can take hours on a NAS CPU) and attaches printed captions to figures. Both are safe to repeat.
- `--keep-env`: keeps the NAS's own `.env` even if the bundle contains `app/.env`.
- `--no-backup`: skips the backup (not recommended).

Rollback: `bash rollback.sh` restarts the previous images and prints how to restore the pre-upgrade backup if you also need the old data.

Optional `.env` settings are listed in `MANIFEST.txt`. The main one is `RERANKER_SECOND_STAGE=` (empty), which turns off the MedCPT second-stage reranker if answers are slow on the NAS CPU.

---

## Alternative: build on the NAS from a git checkout

Run these on the NAS machine itself (SSH into the NAS, then run in the app
directory). The NAS already runs Docker with the compose v5 plugin, which reads
environment variables from `--env-file ./.env`.

## 1. Point the checkout at the new repo & pull the launch build

```bash
cd /path/to/medNAMA        # e.g. the folder where the checkout lives

# First deploy after the repo move: re-point origin (NAS still points at the old repo)
git remote -v
git remote set-url origin https://github.com/itskaero/medNAMA.git

git pull origin main
```

The pull brings in: F1–F6 backend (batch quiz / dedup, notes, flashcards,
exports), the new frontend (SourcesPanel, Study Corner, MCQs 5/10/15/20, drill),
the `docker/Dockerfile`s, `NAS/docker-compose.yaml`, and the alembic migration
`a1b2c3d4e5f6_add_notes_and_flashcards`.

## 2. Prepare `.env` (never commit this file)

Create/update `.env` NEXT TO the compose file (or in the repo root as configured
below). Must contain at least:

```bash
POSTGRES_USER=medrag
POSTGRES_PASSWORD=<strong-db-password>     # must NOT be the placeholder
POSTGRES_DB=medrag
DEEPSEEK_API_KEY=<your-key>
JWT_SECRET=<long-random-string-not-'change-me-in-production'>
GEMINI_API_KEY=                            # optional
ALLOWED_ORIGINS=http://192.168.1.44:3000,http://localhost:3000
MEDNAMA_PORT=3000
```

`ALLOWED_ORIGINS` must include the URL users open in the browser on the LAN
(`http://192.168.1.44:3000`). The backend starts with `--timeout-keep-alive 70`
and CPU-only torch — first `up` takes several minutes while the models warm up.

## 3. Build & start

```bash
# compose v5 syntax (env file via --env-file; do NOT omit it)
docker compose --env-file ./.env -f NAS/docker-compose.yaml up -d --build
```

Place `.env` in the repo root so `postgres` data volume path resolution and the
compose env interpolation both work. The backend applies alembic migrations
automatically on boot (`a1b2c3d4e5f6` creates `notes` + `flashcards`).

## 4. Verify

```bash
docker compose -f NAS/docker-compose.yaml ps        # db, backend, frontend all Up
docker compose -f NAS/docker-compose.yaml logs backend --tail 20
# expect 'Uvicorn running on http://0.0.0.0:8000' and 'Application startup complete.'

curl -s http://192.168.1.44:3000/api/books | head -c 120
```

Then from the browser on any LAN device: `http://192.168.1.44:3000`, log in
(`admin` / your password), and run a chat query → the "All Matched Sources"
panel should appear under the answer.

## 5. Database

If you're migrating the knowledge base, follow `DB_TRANSFER_TO_NAS.md`:
`scripts/export_db.py` locally → copy `medrag.dump` → `bash scripts/restore_nas.sh`
on the NAS → point `DATABASE_URL` at `medrag_new` in `.env`;
`DATABASE_URL=postgresql://medrag:<password>@db:5432/medrag_new`.

## Gotchas

- **First boot is slow**: backend health becomes `healthy` only after the
  embedding + reranker models finish INT8 warmup (~5 min on CPU). Wait for it
  before judging the stack.
- **Proxy dead-socket 500s**: the Next.js rewrite can occasionally reply `500
  Internal Server Error` (plain text) on a pooled keep-alive socket. The
  frontend retries these automatically (`proxySafeFetch`).
- **Do not publish 3000 on 0.0.0.0** if you don't want LAN exposure — keep the
  default mapping and rely on firewall rules if that's the intent.