#!/bin/sh
# medNAMA's start-up (migrations), then PaedNAMA's server instead of plain medNAMA: the same API plus /api/paeds.
set -e

echo "[paednama] applying DB migrations (alembic upgrade head)..."
cd /app/backend && alembic upgrade head

echo "[paednama] starting medNAMA + PaedNAMA API on port ${PORT:-8000}..."
cd /app
export PAEDNAMA_HOST=0.0.0.0 PAEDNAMA_PORT="${PORT:-8000}"
exec python -m apps.paednama.server.main
