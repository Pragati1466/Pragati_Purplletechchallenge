"""
Async database connection using SQLAlchemy 2.0.

Supports both:
  - PostgreSQL (production, via asyncpg)
  - SQLite    (tests and local dev, via aiosqlite)

Tables are created automatically on startup via init_db().
"""

from __future__ import annotations
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text
import structlog

logger = structlog.get_logger()

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://storeuser:storepass@localhost:5432/store_intelligence",
)

_IS_SQLITE = "sqlite" in DATABASE_URL

engine = create_async_engine(
    DATABASE_URL,
    # SQLite doesn't support pool_size / max_overflow
    **({} if _IS_SQLITE else {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_pre_ping": True,
    }),
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# ── Portable DDL (works on both PostgreSQL and SQLite) ────────────────────────
_CREATE_TABLES_SQL = [
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id    TEXT PRIMARY KEY,
        store_id    TEXT NOT NULL,
        camera_id   TEXT NOT NULL,
        visitor_id  TEXT NOT NULL,
        event_type  TEXT NOT NULL,
        timestamp   TEXT NOT NULL,
        zone_id     TEXT,
        dwell_ms    INTEGER DEFAULT 0,
        is_staff    INTEGER DEFAULT 0,
        confidence  REAL    NOT NULL,
        metadata    TEXT    DEFAULT '{}',
        created_at  TEXT    DEFAULT '1970-01-01'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_store_ts   ON events(store_id, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_events_visitor    ON events(visitor_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_type       ON events(event_type)",
    "CREATE INDEX IF NOT EXISTS idx_events_store_date ON events(store_id, DATE(timestamp))",
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id      TEXT PRIMARY KEY,
        store_id        TEXT    NOT NULL,
        visitor_id      TEXT    NOT NULL UNIQUE,
        entry_time      TEXT    NOT NULL,
        exit_time       TEXT,
        zones_visited   TEXT    DEFAULT '[]',
        total_dwell_ms  INTEGER DEFAULT 0,
        converted       INTEGER DEFAULT 0,
        is_staff        INTEGER DEFAULT 0,
        reentry_count   INTEGER DEFAULT 0,
        updated_at      TEXT    DEFAULT '1970-01-01',
        created_at      TEXT    DEFAULT '1970-01-01'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_store_date ON sessions(store_id, DATE(entry_time))",
    "CREATE INDEX IF NOT EXISTS idx_sessions_visitor    ON sessions(visitor_id)",
    """
    CREATE TABLE IF NOT EXISTS pos_transactions (
        transaction_id   TEXT PRIMARY KEY,
        store_id         TEXT NOT NULL,
        timestamp        TEXT NOT NULL,
        basket_value_inr REAL NOT NULL,
        session_id       TEXT,
        created_at       TEXT DEFAULT '1970-01-01'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pos_store_ts ON pos_transactions(store_id, timestamp)",
    """
    CREATE TABLE IF NOT EXISTS anomalies (
        anomaly_id       TEXT PRIMARY KEY,
        store_id         TEXT NOT NULL,
        anomaly_type     TEXT NOT NULL,
        severity         TEXT NOT NULL,
        detected_at      TEXT NOT NULL,
        current_value    REAL,
        baseline_value   REAL,
        suggested_action TEXT,
        resolved         INTEGER DEFAULT 0,
        resolved_at      TEXT,
        created_at       TEXT DEFAULT '1970-01-01'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stores (
        store_id    TEXT PRIMARY KEY,
        store_name  TEXT NOT NULL,
        city        TEXT NOT NULL,
        open_hours  TEXT NOT NULL,
        zones       TEXT NOT NULL,
        cameras     TEXT NOT NULL,
        created_at  TEXT DEFAULT '1970-01-01'
    )
    """,
]

# PostgreSQL-specific DDL (run only when not SQLite)
_PG_EXTRAS = [
    "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"",
    "CREATE INDEX IF NOT EXISTS idx_events_metadata ON events USING GIN (metadata)",
]


async def init_db() -> None:
    """
    Create all tables and verify connectivity.
    Safe to call multiple times (all statements use IF NOT EXISTS / ON CONFLICT DO NOTHING).
    """
    try:
        async with engine.begin() as conn:
            # Create tables
            for stmt in _CREATE_TABLES_SQL:
                stmt = stmt.strip()
                if stmt:
                    await conn.execute(text(stmt))

            # PostgreSQL extras (GIN index, uuid extension)
            if not _IS_SQLITE:
                for stmt in _PG_EXTRAS:
                    try:
                        await conn.execute(text(stmt))
                    except Exception:
                        pass  # non-fatal

            # Seed stores — use dialect-appropriate upsert
            await _seed_stores(conn)

        logger.info("database_connected", url=DATABASE_URL.split("@")[-1])
    except Exception as exc:
        logger.error("database_connection_failed", error=str(exc))
        raise


async def _seed_stores(conn) -> None:
    """Insert default stores if they don't exist yet."""
    stores = [
        (
            "ST1008",
            "Brigade_Bangalore",
            "Bangalore",
            '{"open":"10:00","close":"22:00"}',
            '["MAYBELLINE","LAKME","FACES_CANADA","MARS_NYBAE","ALPS_GOODNESS",'
            '"LOREAL","BEAUTY_ESSENTIALS","ACCESSORIES","JUICY_CHEMISTRY",'
            '"AQUALOGICA","TFS","GOOD_VIBES","DERMDOC","FOXTALE","MINIMALIST",'
            '"MENS_CARE","SWISS_BEAUTY","RENEE","PILGRIM","SALM_EB",'
            '"COSRX_KOREAN","BILLING"]',
            '["CAM_ENTRY_01","CAM_FLOOR_01","CAM_FLOOR_02","CAM_BILLING_01","CAM_FLOOR_03"]',
        ),
        (
            "STORE_BLR_002",
            "Purplle Bangalore - Koramangala",
            "Bangalore",
            '{"open":"10:00","close":"22:00"}',
            '["SKINCARE","MAKEUP","FRAGRANCE","BILLING"]',
            '["CAM_ENTRY_01","CAM_FLOOR_01","CAM_BILLING_01"]',
        ),
    ]

    if _IS_SQLITE:
        upsert = text("""
            INSERT OR IGNORE INTO stores
                (store_id, store_name, city, open_hours, zones, cameras)
            VALUES (:sid, :name, :city, :hours, :zones, :cameras)
        """)
    else:
        upsert = text("""
            INSERT INTO stores (store_id, store_name, city, open_hours, zones, cameras)
            VALUES (:sid, :name, :city, :hours, :zones, :cameras)
            ON CONFLICT (store_id) DO NOTHING
        """)

    for sid, name, city, hours, zones, cameras in stores:
        await conn.execute(upsert, {
            "sid": sid, "name": name, "city": city,
            "hours": hours, "zones": zones, "cameras": cameras,
        })


async def close_db() -> None:
    await engine.dispose()
    logger.info("database_disconnected")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Context-manager version for use outside FastAPI."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
