"""
Pytest fixtures shared across all test modules.

Uses an in-memory SQLite database (via aiosqlite) so tests run without
a live PostgreSQL instance. Each test gets its own engine + tables,
completely isolated — no env var mutation, no module reloads.
"""

from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text

TEST_STORE_ID = "STORE_BLR_002"
TEST_DB_URL   = "sqlite+aiosqlite:///:memory:"

# ── Minimal portable DDL (SQLite-compatible) ──────────────────────────────────
_DDL = [
    """CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        store_id TEXT NOT NULL,
        camera_id TEXT NOT NULL,
        visitor_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        zone_id TEXT,
        dwell_ms INTEGER DEFAULT 0,
        is_staff INTEGER DEFAULT 0,
        confidence REAL NOT NULL,
        metadata TEXT DEFAULT '{}',
        created_at TEXT DEFAULT '1970-01-01'
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        store_id TEXT NOT NULL,
        visitor_id TEXT NOT NULL UNIQUE,
        entry_time TEXT NOT NULL,
        exit_time TEXT,
        zones_visited TEXT DEFAULT '[]',
        total_dwell_ms INTEGER DEFAULT 0,
        converted INTEGER DEFAULT 0,
        is_staff INTEGER DEFAULT 0,
        reentry_count INTEGER DEFAULT 0,
        updated_at TEXT DEFAULT '1970-01-01',
        created_at TEXT DEFAULT '1970-01-01'
    )""",
    """CREATE TABLE IF NOT EXISTS pos_transactions (
        transaction_id TEXT PRIMARY KEY,
        store_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        basket_value_inr REAL NOT NULL,
        session_id TEXT,
        created_at TEXT DEFAULT '1970-01-01'
    )""",
    """CREATE TABLE IF NOT EXISTS anomalies (
        anomaly_id TEXT PRIMARY KEY,
        store_id TEXT NOT NULL,
        anomaly_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        detected_at TEXT NOT NULL,
        current_value REAL,
        baseline_value REAL,
        suggested_action TEXT,
        resolved INTEGER DEFAULT 0,
        resolved_at TEXT,
        created_at TEXT DEFAULT '1970-01-01'
    )""",
    """CREATE TABLE IF NOT EXISTS stores (
        store_id TEXT PRIMARY KEY,
        store_name TEXT NOT NULL,
        city TEXT NOT NULL,
        open_hours TEXT NOT NULL,
        zones TEXT NOT NULL,
        cameras TEXT NOT NULL,
        created_at TEXT DEFAULT '1970-01-01'
    )""",
    # Seed test stores
    """INSERT OR IGNORE INTO stores
        (store_id, store_name, city, open_hours, zones, cameras)
       VALUES
        ('STORE_BLR_002', 'Purplle Bangalore - Koramangala', 'Bangalore',
         '{"open":"10:00","close":"22:00"}',
         '["SKINCARE","MAKEUP","FRAGRANCE","BILLING"]',
         '["CAM_ENTRY_01","CAM_FLOOR_01","CAM_BILLING_01"]')""",
    """INSERT OR IGNORE INTO stores
        (store_id, store_name, city, open_hours, zones, cameras)
       VALUES
        ('ST1008', 'Brigade_Bangalore', 'Bangalore',
         '{"open":"10:00","close":"22:00"}',
         '["MAYBELLINE","LAKME","FACES_CANADA","GOOD_VIBES","DERMDOC","MINIMALIST","BILLING"]',
         '["CAM_ENTRY_01","CAM_FLOOR_01","CAM_BILLING_01"]')""",
]


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def test_engine():
    """Fresh in-memory SQLite engine with all tables created."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        for stmt in _DDL:
            await conn.execute(text(stmt))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db(test_engine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(
        bind=test_engine, class_=AsyncSession,
        expire_on_commit=False, autoflush=False,
    )
    async with factory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(test_engine) -> AsyncGenerator[AsyncClient, None]:
    """
    HTTP test client with the DB dependency overridden to use the
    isolated in-memory test engine. No module reloads, no env var mutation.
    """
    from app.main import app
    from app.database import get_db

    factory = async_sessionmaker(
        bind=test_engine, class_=AsyncSession,
        expire_on_commit=False, autoflush=False,
    )

    async def override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


# ── Event factory ─────────────────────────────────────────────────────────────

def make_event(
    store_id: str = TEST_STORE_ID,
    camera_id: str = "CAM_ENTRY_01",
    visitor_id: str = "VIS_test001",
    event_type: str = "ENTRY",
    timestamp: str = None,
    zone_id: str = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 0.90,
    metadata: dict = None,
) -> dict:
    if timestamp is None:
        # Past timestamp — avoids future-timestamp validator rejection
        ts = datetime.now(timezone.utc).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        timestamp = ts.isoformat().replace("+00:00", "Z")
    if metadata is None:
        metadata = {"session_seq": 1}
    return {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store_id,
        "camera_id":  camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp":  timestamp,
        "zone_id":    zone_id,
        "dwell_ms":   dwell_ms,
        "is_staff":   is_staff,
        "confidence": confidence,
        "metadata":   metadata,
    }
