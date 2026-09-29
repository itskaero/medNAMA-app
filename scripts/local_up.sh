#!/usr/bin/env bash
# Build and start the local NAS-style stack (http://localhost:3000) against the dev database on :5434.
#
#   bash scripts/local_up.sh              # build + start
#   bash scripts/local_up.sh backend      # only the backend
#
# Always passes the repo-root .env. NAS/docker-compose.yaml fills DEEPSEEK_API_KEY etc. from
# ${VAR:-}: without --env-file .env compose reads NAS/.env (absent), the key is empty, and every AI
# feature (twists, hardening, summaries, the referee) fails with "The AI service is not configured".
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "No .env in $(pwd): the backend would start without its AI key."; exit 1; }
[ -f docker-compose.local.yaml ] || { echo "docker-compose.local.yaml (local-only) is missing."; exit 1; }
docker compose --env-file .env -f NAS/docker-compose.yaml -f docker-compose.local.yaml up -d --build "$@"
