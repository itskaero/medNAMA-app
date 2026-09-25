#!/bin/bash
# Roll the medNAMA app back to the images that were running before the last deploy.sh.
# Run as root ON the NAS:   bash rollback.sh
#
# This restores the previous app code only. If the new version had already
# migrated the database and you need the old data back, restore the backup
# deploy.sh made (see the end of this script).
set -euo pipefail

APP="${MEDNAMA_APP_DIR:-/DATA/mednama/app}"
BACKUP_DIR="${MEDNAMA_BACKUP_DIR:-/DATA/mednama/backups}"

for img in app-backend app-frontend; do
  if ! docker image inspect "$img:previous" >/dev/null 2>&1; then
    echo "ERROR: $img:previous not found - nothing to roll back to."; exit 1
  fi
done

docker tag app-backend:previous app-backend:latest
docker tag app-frontend:previous app-frontend:latest
if [ -f "$APP/.previous_db" ]; then
  # deploy.sh --restore-db switched to a new database; go back to the one it replaced.
  PREV_DB="$(cat "$APP/.previous_db")"
  sed -i "s/^POSTGRES_DB=.*/POSTGRES_DB=$PREV_DB/" "$APP/.env"
  rm -f "$APP/.previous_db"
  echo "Database switched back to $PREV_DB (the restored one is kept; drop it later if unwanted)."
  RECREATE_DB=1
fi
PROJECT="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "${MEDNAMA_DB_CONTAINER:-mednama-db}" 2>/dev/null || true)"
PROJECT="${PROJECT:-$(basename "$APP")}"
cd "$APP"
if [ "${RECREATE_DB:-0}" = 1 ]; then
  docker compose -p "$PROJECT" --env-file ./.env -f docker-compose.yml up -d --force-recreate
else
  docker compose -p "$PROJECT" --env-file ./.env -f docker-compose.yml up -d --force-recreate backend frontend
fi
echo "Rolled back to the previous images. Backend warm-up takes a few minutes:"
echo "  docker compose -f $APP/docker-compose.yml ps"

LATEST="$(ls -1t "$BACKUP_DIR"/medrag-*.dump 2>/dev/null | head -1 || true)"
if [ -n "$LATEST" ]; then
  echo
  echo "Database backup taken before the last deploy: $LATEST"
  echo "Only if you need the pre-upgrade DATA back (older code usually runs fine on the"
  echo "newer schema, because the migrations only add columns/tables):"
  echo "  bash $(cd "$(dirname "$0")" && pwd)/restore_nas.sh $LATEST medrag_restore"
  echo "  then set POSTGRES_DB=medrag_restore in $APP/.env and run: docker compose -f $APP/docker-compose.yml up -d"
fi
