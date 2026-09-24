#!/bin/sh
set -e

echo "[entrypoint] applying DB migrations (alembic upgrade head)..."
alembic upgrade head

echo "[entrypoint] starting Uvicorn on port ${PORT:-8000}..."
# --timeout-keep-alive: keep idle connections alive longer than the 5s default.
# The Next.js proxy reuses pooled keep-alive sockets; if uvicorn closes them
# early, the first request after idle dies with a socket hang-up before
# reaching the backend (surfaces as 500 in the UI).
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --timeout-keep-alive 70