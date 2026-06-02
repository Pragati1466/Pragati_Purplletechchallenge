#!/bin/bash
# start.sh — Render startup script
set -e

export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///./store_intelligence.db}"
export REDIS_URL="${REDIS_URL:-}"
export PORT="${PORT:-10000}"

echo "==> DATABASE_URL: $DATABASE_URL"
echo "==> PORT: $PORT"
echo "==> Starting Store Intelligence API..."

exec python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
