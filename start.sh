#!/bin/bash
# start.sh — Render startup script
set -e

export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///./store_intelligence.db}"
export REDIS_URL="${REDIS_URL:-}"

echo "==> DATABASE_URL: $DATABASE_URL"
echo "==> Starting API (init_db will create tables and seed stores)..."

exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
