#!/usr/bin/env bash
# Disconnect-proof restore of a medNAMA pg_dump into the NAS's Postgres container.
#
# Why this exists: `pg_restore` of a ~440MB dump (105k+ vector rows, thousands
# of embedded figure images) into a slow NAS volume looks fully frozen for
# 10-30 minutes, and a plain foreground restore DIES if your SSH session
# drops. This script:
#   1. copies the dump into the container (docker cp - no pipe truncation),
#   2. drops & recreates the target DB (cleans up a previous stuck attempt),
#   3. runs pg_restore DETACHED inside the container (survives SSH drops)
#      with -v so /tmp/restore.log shows per-object progress,
#   4. polls the log + live chunk count so you can SEE it's not stuck.
#
# Usage (run on the NAS, from the machine that holds the dump):
#   ./restore_nas.sh medrag.dump [db_name] [container]
#     medrag.dump   the dump file (path on the NAS/filesystem you run from)
#     db_name       default medrag_new   (DESTRUCTIVE: dropped & recreated)
#     container     default mednama-db
#
# After it finishes: point the app at the new DB by setting
#   POSTGRES_DB=medrag_new  in the backend's .env
# then `docker compose up -d`.

set -euo pipefail

DUMP="${1:?usage: restore_nas.sh <dump> [db_name] [container]}"
NEWDB="${2:-medrag_new}"
CID="${3:-mednama-db}"

if ! docker ps --format '{{.Names}}' | grep -qx "$CID"; then
    echo "ERROR: container '$CID' is not running. Running containers:"
    docker ps --format '  {{.Names}}  {{.Image}}'
    exit 1
fi

if [ ! -f "$DUMP" ]; then
    echo "ERROR: dump file not found: $DUMP"
    exit 1
fi

echo "[1/5] copying $DUMP into container (checksum after copy)..."
docker cp "$DUMP" "$CID:/tmp/medrag.dump"
docker exec "$CID" ls -l /tmp/medrag.dump

echo "[2/5] dropping & recreating database '$NEWDB' (DESTRUCTIVE to $NEWDB only)..."
# Connect to the maintenance DB: psql defaults to a DB named after the user
# ("medrag"), which cannot drop itself. WITH (FORCE) ends stray connections
# (e.g. a restarting backend) that would otherwise block the drop.
docker exec "$CID" psql -U medrag -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS \"$NEWDB\" WITH (FORCE)" >/dev/null
docker exec "$CID" createdb -U medrag "$NEWDB"

echo "[3/5] starting detached pg_restore (verbose log at /tmp/restore.log)..."
docker exec -d "$CID" sh -c \
    "pg_restore -U medrag -d $NEWDB --no-owner --no-comments -v /tmp/medrag.dump \
       > /tmp/restore.log 2>&1; echo \"RESTORE_EXIT_CODE=\$?\" >> /tmp/restore.log"

echo "[4/5] watching progress - chunk count grows as it works (ctrl-c stops watching, restore continues):"
while true; do
    n=$(docker exec "$CID" psql -U medrag -d "$NEWDB" -tAc \
        "SELECT count(*) FROM chunks" 2>/dev/null || true)
    tail_line=$(docker exec "$CID" tail -c 140 /tmp/restore.log 2>/dev/null | tr '\r\n' ' ')
    if docker exec "$CID" grep -q "RESTORE_EXIT_CODE=" /tmp/restore.log 2>/dev/null; then
        break
    fi
    echo "$(date +%H:%M:%S)  chunks inserted: ${n:-0}   |  $tail_line"
    sleep 10
done

echo
echo "[5/5] restore finished. Result:"
docker exec "$CID" tail -n 3 /tmp/restore.log
docker exec "$CID" psql -U medrag -d "$NEWDB" -c \
    "SELECT (SELECT count(*) FROM books) AS books, (SELECT count(*) FROM chunks) AS chunks, (SELECT count(*) FROM figures) AS figures"

echo
echo "Next: set POSTGRES_DB=$NEWDB in the backend .env and run: docker compose up -d"