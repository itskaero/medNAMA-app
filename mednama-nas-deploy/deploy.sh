#!/bin/bash
# medNAMA NAS deploy (image-based, no build on the NAS).
# Run as root ON the NAS, from inside this folder:
#
#     bash deploy.sh                # normal upgrade
#     bash deploy.sh --data-fixes   # also run one-off data fixes (safe to repeat):
#                                   #   Bailey & Love lost-ligature repair + re-embed,
#                                   #   printed-caption backfill for figures
#     bash deploy.sh --no-backup    # skip the pre-upgrade DB backup (not recommended)
#     bash deploy.sh --keep-env     # keep the NAS's current .env even if app/.env is bundled
#     bash deploy.sh --restore-db   # replace the NAS database with the bundled PC dump (db/*.dump):
#                                   #   restores into a NEW database (the current one is kept for
#                                   #   rollback), copies this NAS's users/chats/attempts/bookmarks
#                                   #   into it, then switches POSTGRES_DB. Combine with --keep-env.
#
# Steps: verify checksums -> back up the DB -> keep the current images as
# ":previous" (see rollback.sh) -> load + tag the new images -> start ->
# wait until healthy -> smoke test.
set -euo pipefail

APP="${MEDNAMA_APP_DIR:-/DATA/mednama/app}"
BACKUP_DIR="${MEDNAMA_BACKUP_DIR:-/DATA/mednama/backups}"
HF_DIR="${MEDNAMA_HF_DIR:-/DATA/mednama/huggingface}"
DB_CONTAINER="${MEDNAMA_DB_CONTAINER:-mednama-db}"
BACKEND_CONTAINER="${MEDNAMA_BACKEND_CONTAINER:-mednama-backend}"
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

DATA_FIXES=0
DO_BACKUP=1
KEEP_ENV=0
RESTORE_DB=0
for arg in "$@"; do
  case "$arg" in
    --data-fixes) DATA_FIXES=1 ;;
    --repair-bailey) DATA_FIXES=1 ;;   # older name
    --no-backup) DO_BACKUP=0 ;;
    --keep-env) KEEP_ENV=1 ;;
    --restore-db) RESTORE_DB=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

step() { echo; echo "==> $*"; }
env_value() {  # read KEY from the deployed .env without sourcing it
  local key="$1" default="$2" file="$APP/.env"
  local v=""
  [ -f "$file" ] && v="$(grep -E "^${key}=" "$file" | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//')"
  echo "${v:-$default}"
}

for f in mednama-backend.tar mednama-frontend.tar app/docker-compose.yml; do
  [ -f "$f" ] || { echo "ERROR: $f not found next to this script."; exit 1; }
done
DUMP_FILE=""
if [ "$RESTORE_DB" = 1 ]; then
  DUMP_FILE="$(ls -1 db/*.dump 2>/dev/null | head -1 || true)"
  [ -n "$DUMP_FILE" ] || { echo "ERROR: --restore-db given but there is no db/*.dump in this bundle."; exit 1; }
fi
[ -f MANIFEST.txt ] && { echo "----- bundle -----"; cat MANIFEST.txt; echo "------------------"; }

step "[1/8] Verifying image checksums"
if [ -f SHA256SUMS ]; then
  sha256sum -c SHA256SUMS
else
  echo "WARNING: no SHA256SUMS - cannot verify the copy (re-create the bundle with scripts/make_nas_bundle.sh)."
fi

step "[2/8] Installing compose file + .env into $APP"
mkdir -p "$APP/docker"
cp app/docker-compose.yml "$APP/docker-compose.yml"
if [ -f app/.env ] && [ "$KEEP_ENV" = 0 ]; then
  cp app/.env "$APP/.env"
elif [ ! -f "$APP/.env" ]; then
  echo "ERROR: no .env in the bundle and none on the NAS at $APP/.env"; exit 1
else
  echo "Keeping the existing $APP/.env"
fi
for f in .dockerignore docker/backend-entrypoint.sh docker/backend.Dockerfile; do
  [ -f "app/$f" ] && cp "app/$f" "$APP/$f"
done

# Reuse the compose project that owns the running database, so `compose up`
# adopts the existing containers instead of failing with "name already in use".
PROJECT="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$DB_CONTAINER" 2>/dev/null || true)"
PROJECT="${PROJECT:-$(basename "$APP")}"
echo "Compose project: $PROJECT"

PG_USER="$(env_value POSTGRES_USER medrag)"
PG_DB="$(env_value POSTGRES_DB medrag)"
PORT="$(env_value MEDNAMA_PORT 3000)"

step "[3/8] Backing up the database (the new backend applies migrations on start)"
if [ "$DO_BACKUP" = 1 ]; then
  if docker ps --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
    mkdir -p "$BACKUP_DIR"
    DUMP="$BACKUP_DIR/medrag-$(date +%Y%m%d-%H%M%S).dump"
    docker exec "$DB_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" -Fc -Z6 > "$DUMP"
    if [ "$(head -c 5 "$DUMP")" != "PGDMP" ]; then
      echo "ERROR: backup $DUMP is not a valid pg_dump file - aborting before any change."; exit 1
    fi
    echo "Backup: $DUMP ($(du -h "$DUMP" | cut -f1))"
    # Keep the newest backups only (each is ~1.2 GB); the one just made is always among them.
    KEEP="${MEDNAMA_KEEP_BACKUPS:-5}"
    ls -1t "$BACKUP_DIR"/medrag-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
      echo "Removing old backup $old"; rm -f -- "$old"
    done
  else
    echo "Database container '$DB_CONTAINER' is not running (first install?) - nothing to back up."
  fi
else
  echo "Skipped (--no-backup)."
fi

if [ "$RESTORE_DB" = 1 ]; then
  step "Restoring $DUMP_FILE into a new database (the current '$PG_DB' is left untouched)"
  OLD_DB="$PG_DB"
  NEW_DB="medrag_$(date +%Y%m%d%H%M)"
  docker cp "$DUMP_FILE" "$DB_CONTAINER:/tmp/medrag-restore.dump"
  docker exec "$DB_CONTAINER" psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS \"$NEW_DB\" WITH (FORCE)" >/dev/null
  docker exec "$DB_CONTAINER" createdb -U "$PG_USER" "$NEW_DB"
  docker exec "$DB_CONTAINER" rm -f /tmp/restore.log   # an old log would end the wait loop at once
  # Runs detached inside the DB container, so it survives an SSH drop. Index builds are
  # single-process: Docker's 64 MB /dev/shm is too small for parallel HNSW builds.
  docker exec -d -e PGOPTIONS="-c max_parallel_maintenance_workers=0 -c maintenance_work_mem=${MEDNAMA_INDEX_MEM:-256MB}" \
    "$DB_CONTAINER" sh -c "pg_restore -U $PG_USER -d $NEW_DB --no-owner --no-comments -v /tmp/medrag-restore.dump \
      > /tmp/restore.log 2>&1; echo RESTORE_EXIT_CODE=\$? >> /tmp/restore.log"
  echo "Restoring (10-40 min on a NAS; the site stays up on '$OLD_DB' meanwhile)..."
  while ! docker exec "$DB_CONTAINER" grep -q "RESTORE_EXIT_CODE=" /tmp/restore.log 2>/dev/null; do
    n="$(docker exec "$DB_CONTAINER" psql -U "$PG_USER" -d "$NEW_DB" -At -c 'select count(*) from mcqs' 2>/dev/null || echo 0)"
    last="$(docker exec "$DB_CONTAINER" tail -n 1 /tmp/restore.log 2>/dev/null | cut -c1-90)"
    echo "  $(date +%H:%M:%S)  mcqs restored: ${n:-0}  | $last"
    sleep 30
  done
  ERRS="$(docker exec "$DB_CONTAINER" grep -c 'pg_restore: error' /tmp/restore.log || true)"
  docker exec "$DB_CONTAINER" rm -f /tmp/medrag-restore.dump
  HEAD="$(docker exec "$DB_CONTAINER" psql -U "$PG_USER" -d "$NEW_DB" -At -c 'select version_num from alembic_version' 2>/dev/null || true)"
  if [ -z "$HEAD" ]; then
    echo "ERROR: restore failed (no schema version in $NEW_DB). Log: docker exec $DB_CONTAINER cat /tmp/restore.log"
    exit 1
  fi
  echo "Restored $NEW_DB at schema $HEAD ($ERRS error line(s) in /tmp/restore.log; errors about existing"
  echo "extensions or ownership are harmless)."
  docker exec "$DB_CONTAINER" psql -U "$PG_USER" -d "$NEW_DB" -c \
    "select (select count(*) from books) books, (select count(*) from chunks) chunks, (select count(*) from mcqs) mcqs, (select count(*) from users) users"
fi

step "Vector index (built while the current app keeps running)"
# HNSW index for chunk vector search (migration e5a7c9d1f3b5). Building it here,
# CONCURRENTLY and single-process (Docker's 64 MB /dev/shm is too small for
# parallel builds), avoids a long unhealthy start when the migration would
# otherwise build it. No-op once it exists.
if [ "$RESTORE_DB" = 1 ]; then
  echo "Skipped: the restored database carries its own index."
elif docker ps --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
  HAS_INDEX="$(docker exec "$DB_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -At -c     "select count(*) from pg_index where indexrelid = to_regclass('idx_chunks_child_embedding_hnsw') and indisvalid" 2>/dev/null || echo 0)"
  if [ "$HAS_INDEX" = "1" ]; then
    echo "Already present."
  else
    echo "Building (roughly 5-20 min on a NAS; the site stays up)..."
    docker exec "$DB_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1       -c "DROP INDEX CONCURRENTLY IF EXISTS idx_chunks_child_embedding_hnsw"       -c "SET maintenance_work_mem = '${MEDNAMA_INDEX_MEM:-512MB}'"       -c "SET max_parallel_maintenance_workers = 0"       -c "CREATE INDEX CONCURRENTLY idx_chunks_child_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops) WHERE parent_id IS NOT NULL"
    echo "Vector index built."
  fi
fi

step "[4/8] Keeping current images as :previous (for rollback.sh)"
for img in app-backend app-frontend; do
  if docker image inspect "$img:latest" >/dev/null 2>&1; then
    docker tag "$img:latest" "$img:previous" && echo "  $img:latest -> $img:previous"
  fi
done

step "[5/8] Stopping app containers (database keeps running)"
docker stop mednama-frontend 2>/dev/null || true
docker stop "$BACKEND_CONTAINER" 2>/dev/null || true

step "[6/8] Loading new images (2-4 min on NAS disk)"
docker load -i mednama-backend.tar
docker load -i mednama-frontend.tar
docker tag nas-backend:latest app-backend:latest
docker tag nas-frontend:latest app-frontend:latest
if [ -f hf-medcpt.tar ]; then
  mkdir -p "$HF_DIR/hub"
  tar -xf hf-medcpt.tar -C "$HF_DIR/hub" && echo "MedCPT reranker installed into $HF_DIR/hub"
fi

if [ "$RESTORE_DB" = 1 ]; then
  step "Copying this NAS's users, chats, quiz attempts and bookmarks from '$OLD_DB' into '$NEW_DB'"
  NET="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$DB_CONTAINER" | awk '{print $1}')"
  PG_PASS="$(env_value POSTGRES_PASSWORD change-this-db-password)"
  # A one-off container of the new backend image; merge_user_data.py loads no ML models.
  docker run --rm --network "$NET" --entrypoint python -w /app/backend \
    -e DATABASE_URL="postgresql://$PG_USER:$PG_PASS@$DB_CONTAINER:5432/$NEW_DB" \
    app-backend:latest scripts/merge_user_data.py --source-db "$OLD_DB" --apply
  if grep -q '^POSTGRES_DB=' "$APP/.env"; then
    sed -i "s/^POSTGRES_DB=.*/POSTGRES_DB=$NEW_DB/" "$APP/.env"
  else
    echo "POSTGRES_DB=$NEW_DB" >> "$APP/.env"
  fi
  echo "$OLD_DB" > "$APP/.previous_db"
  PG_DB="$NEW_DB"
  echo "POSTGRES_DB is now $NEW_DB (previous: $OLD_DB, kept; rollback.sh switches back)."
fi

step "[7/8] Starting the stack"
cd "$APP"
docker compose -p "$PROJECT" --env-file ./.env -f docker-compose.yml up -d

echo "Waiting for the backend to become healthy (model warm-up takes a few minutes)..."
STATUS=""
for _ in $(seq 1 120); do
  STATUS="$(docker inspect -f '{{.State.Health.Status}}' "$BACKEND_CONTAINER" 2>/dev/null || echo missing)"
  [ "$STATUS" = "healthy" ] && break
  sleep 5
done
if [ "$STATUS" != "healthy" ]; then
  echo "FAIL: backend is '$STATUS' after 10 minutes. Last log lines:"
  docker logs --tail 40 "$BACKEND_CONTAINER" 2>&1 || true
  echo "Roll back with:  bash $HERE/rollback.sh"
  exit 1
fi
echo "Backend healthy. Schema version: $(docker exec "$DB_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -At -c 'select version_num from alembic_version' 2>/dev/null || echo '?')"

if [ "$DATA_FIXES" = 1 ]; then
  step "Data fixes (idempotent)"
  BAILEY_ID="$(docker exec "$DB_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -At -c "select id from books where title ilike '%bailey%' order by id limit 1" 2>/dev/null || true)"
  if [ -n "$BAILEY_ID" ]; then
    echo "Repairing Bailey & Love (book $BAILEY_ID) lost ligatures + re-embedding (can take a long time)..."
    docker exec "$BACKEND_CONTAINER" python scripts/repair_ligatures.py --book-id "$BAILEY_ID" --apply | tail -3
  else
    echo "No Bailey book found - skipping ligature repair."
  fi
  echo "Attaching printed captions to figures..."
  docker exec "$BACKEND_CONTAINER" python scripts/backfill_figure_captions.py --apply | grep -E "Figures:|Done|Error" || true
fi

step "[8/8] Smoke test through the frontend proxy on :$PORT"
FAILS=0
check() {  # name, url, acceptable codes (regex)
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$2" || echo 000)"
  if echo "$code" | grep -Eq "$3"; then echo "  PASS  $1 ($code)"; else echo "  FAIL  $1 ($code)"; FAILS=$((FAILS + 1)); fi
}
check "frontend page"              "http://127.0.0.1:$PORT/"              '^200$'
check "API via proxy (auth guard)" "http://127.0.0.1:$PORT/api/books"     '^(200|401)$'
check "figure route via proxy"     "http://127.0.0.1:$PORT/api/figures/1" '^(200|401|404)$'

echo
if [ "$FAILS" = 0 ]; then
  echo "DEPLOY OK. Open http://<nas-ip>:$PORT"
else
  echo "DEPLOY FINISHED WITH $FAILS FAILED CHECK(S). Logs: docker logs $BACKEND_CONTAINER | tail -50"
  echo "Roll back with:  bash $HERE/rollback.sh"
  exit 1
fi
