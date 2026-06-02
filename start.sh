#!/bin/bash
# start.sh — used by Render to boot the API with seeded data
set -e

echo "Starting Store Intelligence API..."

# Start the API in background briefly to let init_db() run and create tables
python -c "
import asyncio, sys
sys.path.insert(0, '.')
from app.database import init_db, close_db
async def setup():
    await init_db()
    await close_db()
asyncio.run(setup())
print('DB tables created and stores seeded.')
"

# Seed real Brigade Road data
echo "Seeding real Brigade Road data..."
python data/init_real_data.py --db-url "${DATABASE_URL:-sqlite+aiosqlite:///./store_intelligence.db}" || echo "Seed skipped (already seeded)"

# Start the API
echo "Starting uvicorn..."
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
