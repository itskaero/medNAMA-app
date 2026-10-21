# Transferring the medNAMA database to your NAS

The entire knowledge base — chunks, embeddings, figures, the MCQ bank, and user
data — lives in the PostgreSQL database `medrag`. "Transferring the database"
means taking a consistent snapshot of that one database and loading it into the
Postgres your NAS app uses. Nothing else needs to move (PDFs stay on disk; the
app re-reads them only at ingest time).

Recommended flow, in order:

1. **Ingest everything** on this machine (see `scripts/run_ingestion.py`) so the
   snapshot you move is the final knowledge base.
2. **Export** a snapshot from the local DB (`scripts/export_db.py`).
3. **Copy** the `.dump` file to the NAS (scp / rsync / SMB share).
4. **Restore** it into a *new* database on the NAS and switch the app to it.

---

## Current snapshot (as of 2026-09-21 23:29)

A fresh dump already exists at the repo root:

```
medrag.dump   441.1 MB   (custom-format, -Z9 compressed, header PGDMP verified)
```

Contents of the source database at export time:

| table     | rows   |
|-----------|--------|
| books     | 6      |
| chunks    | 105,496|
| figures   | 6,011  |
| mcqs      | 0      |

-> The MCQ bank is **not seeded** in this dump. If you want board questions on
   the NAS too, run `python backend/scripts/seed_mcqs.py` *before* exporting, or
   re-export after seeding. Everything else (chunks, embeddings, figures,
   users/auth) is included.

> ⚠️ Snapshot timing: this dump was captured **while ingestion was still
> running** — `guyton-hall-physiology.pdf` was mid-re-ingestion, so that book's
> chunks in this dump are partial, and 4 books were still queued (microbiology,
> kaztung-pharmacology, bailey-surgery, snell-anatomy). It will restore and run,
> but for the final knowledge base on the NAS, let `run_ingestion.py` finish and
> re-export once:
>
> ```powershell
> python backend/scripts/export_db.py   # overwrites medrag.dump when the run is done
> ```

---

## 0. Pre-flight checks on the NAS

The dump uses the `vector` type (pgvector), so the NAS Postgres **must ship the
pgvector extension** — the `pgvector/pgvector:pg17` image does. If your NAS app
runs stock Postgres, install pgvector first.

Check from the NAS app directory:

```bash
docker compose exec db psql -U medrag -c \
  "SELECT name, default_version FROM pg_available_extensions WHERE name='vector';"
```

Should print one row. If not, use the `pgvector/pgvector:pg17` image (same as
this machine's `docker-compose.yml`) — keeping the extension version on both
sides identical avoids vector restore surprises.

---

## 1. Export on this machine

Simplest, byte-safe on Windows (PowerShell `>` corrupts binary dumps, this does not):

```powershell
cd E:\Projects\Coding Projects\medNAMA
python backend/scripts/export_db.py                 # -> medrag.dump at repo root
python backend/scripts/export_db.py --out D:\backups\medrag-20260921.dump
```

This shells out to `docker compose exec -T db pg_dump -U medrag -Fc -Z9 -d medrag`
and writes the compressed custom-format backup. Expect a few hundred MB to ~2 GB
depending on how many books are ingested. `pg_dump` reads a **consistent
snapshot**, so it is safe to run even while ingestion is in flight (though a
post-ingestion backup is the one you want on the NAS).

Sanity-check the file:

```powershell
python -c "data=open(r'medrag.dump','rb').read(); print(len(data)//2**20, 'MB -', data[:4])"
```

You should see the magic bytes `PGDMP`.

---

## 2. Copy to the NAS

Any transport works — the file is what matters:

```bash
# scp (NAS has SSH)
scp medrag.dump user@<nas-ip>:/path/to/medNAMA/medrag.dump

# rsync (resumable, better for large files)
rsync -avP medrag.dump user@<nas-ip>:/path/to/medNAMA/medrag.dump

# or just drag-and-drop over your SMB/Windows network share
```

Put it at `<nas-app-dir>/medrag.dump` — anywhere readable by the machine that
runs `docker compose`.

---

## 3. Restore into a new database on the NAS

Never restore *over* the live app DB. Create `medrag_new`, restore into it, then
flip the app over.

> ⚠️ "It's stuck" is usually not stuck: a ~440MB dump with 105k+ vector chunks
> and thousands of embedded figure images into a NAS (often an HDD) can take
> **10–30 minutes with zero output**. And a foreground restore **dies if your
> SSH session drops**, which is what leaves a half-restored `medrag_new` that
> needs a clean redo. Use the disconnect-proof script below.

On the NAS, inside the medNAMA app directory where `docker-compose.yml` lives
(backend checked out there too, so `scripts/` is available), with the dump file
next to it:

```bash
bash scripts/restore_nas.sh medrag.dump          # -> medrag_new in container mednama-db
```

What it does, in one line each:

1. `docker cp medrag.dump mednama-db:/tmp/medrag.dump` — byte-safe, no pipe truncation.
2. Drops & recreates `medrag_new` (cleans up any previous stuck attempt — this is
   why retrying works now), then `createdb`.
3. Runs `pg_restore` **detached inside the container** with `-v` — survives SSH
   drops; progress is logged to `/tmp/restore.log`.
4. Polls the log + prints the **live `chunks` count every 10s** so you can see
   the numbers climbing while it works.
5. Prints the final row counts when `RESTORE_EXIT_CODE=0` appears.

If your SSH session drops mid-restore, the restore keeps running. Re-attach and
watch it any time with:

```bash
docker exec mednama-db tail -f /tmp/restore.log
docker exec mednama-db psql -U medrag -d medrag_new -tAc "SELECT count(*) FROM chunks"
```

(Alternate manual method, if you prefer not to use the script:)

```bash
python scripts/export_db.py --restore --dump medrag.dump --db medrag_new
```

Verify on the NAS:

```bash
docker compose exec db psql -U medrag -d medrag_new -c "\dt"
docker compose exec db psql -U medrag -d medrag_new -c \
  "SELECT (SELECT count(*) FROM books)  AS books,
          (SELECT count(*) FROM chunks) AS chunks,
          (SELECT count(*) FROM figures) AS figures,
          (SELECT count(*) FROM mcqs)  AS mcqs;"

# confirm vectors restored (shows the embedding dimension)
docker compose exec db psql -U medrag -d medrag_new -c \
  "SELECT column_name, udt_name FROM information_schema.columns
    WHERE table_name='chunks' AND column_name='embedding';"
```

---

## 4. Switch the app to the restored database

1. Stop the backend (and anything writing to the DB).
2. Edit `DATABASE_URL` in the `.env` of the NAS app:

   ```
   DATABASE_URL=postgresql://medrag:medrag@db:5432/medrag_new
   ```

   (Inside the docker-compose network the host is `db`, not `localhost`.)
3. Restart: `docker compose up -d` (or `docker compose restart backend`).
4. Smoke-test on the NAS app: ask a Q&A question, open a figure, load the MCQ
   bank, and check quiz history.

Once everything verifies, you can drop the old DB:

```bash
docker compose exec db psql -U medrag -c "DROP DATABASE medrag;"
```

---

## Why not copy the Docker volume instead?

Copying the `pgdata` volume (`pgdata:/var/lib/postgresql/data`) between machines
only works when both sides run the exact same Postgres major version and the
database is stopped — and a half-backed-up volume corrupts everything. The
`pg_dump` route is version-tolerant and can be re-run safely any time, so it's
the recommended path. (If the NAS Postgres major version differs from `pg17`,
logical restore is also what lets you move across majors.)

## Tips

- **Scheduling**: snapshot the DB right after a finished ingestion run; keep old
  dumps (they compress well, `-Z9`).
- **Large dumps**: for a 2 GB dump, `rsync` is resumable if your transfer drops.
- **Auth notes**: users, passwords (bcrypt) and JWT setup travel inside the dump,
  so login data moves with it. `--no-owner` means object owners are set to the
  restoring user (`medrag`), matching the app.

## 5. Rerunning the Docker stack after the transfer

This machine (`docker-compose.yml` runs the Postgres service; the backend runs
locally with `uvicorn`):

```powershell
cd E:\Projects\Coding Projects\medNAMA
docker compose up -d            # start/restart the db container (keeps pgdata volume)
docker compose ps               # mednama-db-1 should be Up
# backend (local, normal dev flow):
cd backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

On the NAS, after `--restore` and updating `DATABASE_URL` in `.env`:

```bash
docker compose up -d            # recreates containers against the new DB
# or, quicker:
docker compose restart backend
```

Smoke-test the NAS app: ask a Q&A question, open a figure, load the MCQ bank,
check quiz history. Once happy, drop the old local DB on the NAS
(`docker compose exec db psql -U medrag -c "DROP DATABASE medrag;"`).