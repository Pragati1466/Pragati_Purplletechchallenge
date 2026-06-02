"""
GET /stores/{store_id}/metrics

Returns real-time store metrics for today (or a given date).
Staff events (is_staff=true) are excluded from all customer metrics.
Results are cached in Redis for 30 seconds; cache is invalidated on ingest.

All SQL uses portable syntax (DATE(), CAST, no AT TIME ZONE, no FILTER clause)
so the same queries work on both PostgreSQL (production) and SQLite (tests).
"""

from __future__ import annotations
import json
import os
from datetime import date, datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import StoreMetrics

logger = structlog.get_logger()
router = APIRouter()

# ── Redis (optional — graceful fallback if unavailable) ──────────────────────
try:
    import redis.asyncio as aioredis
    _redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    _redis = aioredis.from_url(_redis_url, decode_responses=True)
    REDIS_AVAILABLE = True
except Exception:
    _redis = None
    REDIS_AVAILABLE = False

CACHE_TTL = 30  # seconds


# ── SQL queries (portable — works on PostgreSQL and SQLite) ───────────────────

_Q_UNIQUE_VISITORS = text("""
    SELECT COUNT(DISTINCT visitor_id)
    FROM sessions
    WHERE store_id = :store_id
      AND DATE(entry_time) = :date
      AND NOT is_staff
""")

_Q_CONVERTED = text("""
    SELECT COUNT(DISTINCT s.visitor_id)
    FROM sessions s
    JOIN pos_transactions p ON p.session_id = s.session_id
    WHERE s.store_id = :store_id
      AND DATE(s.entry_time) = :date
      AND NOT s.is_staff
""")

_Q_AVG_DWELL = text("""
    SELECT zone_id, CAST(AVG(dwell_ms) AS INTEGER) AS avg_dwell
    FROM events
    WHERE store_id = :store_id
      AND DATE(timestamp) = :date
      AND event_type = 'ZONE_DWELL'
      AND NOT is_staff
      AND zone_id IS NOT NULL
    GROUP BY zone_id
""")

# queue_depth stored in metadata JSON string — use json_extract (SQLite + PG 12+)
_Q_QUEUE_DEPTH = text("""
    SELECT json_extract(metadata, '$.queue_depth') AS depth
    FROM events
    WHERE store_id = :store_id
      AND event_type = 'BILLING_QUEUE_JOIN'
      AND NOT is_staff
    ORDER BY timestamp DESC
    LIMIT 1
""")

# Portable COUNT with CASE instead of FILTER (FILTER is PG/SQLite 3.30+)
_Q_ABANDONMENT = text("""
    SELECT
        SUM(CASE WHEN event_type = 'BILLING_QUEUE_ABANDON' THEN 1 ELSE 0 END) AS abandoned,
        SUM(CASE WHEN event_type = 'BILLING_QUEUE_JOIN'    THEN 1 ELSE 0 END) AS joined
    FROM events
    WHERE store_id = :store_id
      AND DATE(timestamp) = :date
      AND NOT is_staff
""")


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/{store_id}/metrics", response_model=StoreMetrics)
async def get_metrics(
    request: Request,
    store_id: str,
    date_str: Optional[str] = Query(None, alias="date",
                                    description="YYYY-MM-DD (default: today UTC)"),
    db: AsyncSession = Depends(get_db),
) -> StoreMetrics:
    """Real-time store metrics. Staff excluded. Cached 30 s."""
    target_date = _parse_date(date_str)
    cache_key = f"metrics:{store_id}:{target_date}"

    # Try cache
    cached = await _cache_get(cache_key)
    if cached:
        return StoreMetrics(**json.loads(cached))

    metrics = await compute_store_metrics(store_id, str(target_date), db)

    # Store in cache
    await _cache_set(cache_key, metrics.model_dump_json(), CACHE_TTL)
    return metrics


# ── Core computation (also used by tests) ────────────────────────────────────

async def compute_store_metrics(
    store_id: str,
    date_str: str,
    db: AsyncSession,
) -> StoreMetrics:
    """Compute metrics from DB. Raises ValueError for invalid store_id."""
    if not store_id or len(store_id) > 50:
        raise ValueError(f"Invalid store_id: {store_id!r}")

    params = {"store_id": store_id, "date": date_str}

    # Unique visitors
    row = (await db.execute(_Q_UNIQUE_VISITORS, params)).fetchone()
    unique_visitors: int = int(row[0]) if row and row[0] else 0

    # Conversion rate
    conv_row = (await db.execute(_Q_CONVERTED, params)).fetchone()
    converted: int = int(conv_row[0]) if conv_row and conv_row[0] else 0
    conversion_rate = round(converted / unique_visitors, 4) if unique_visitors > 0 else 0.0

    # Avg dwell per zone
    dwell_rows = (await db.execute(_Q_AVG_DWELL, params)).fetchall()
    avg_dwell_per_zone = {r[0]: int(r[1]) for r in dwell_rows if r[0]}

    # Current queue depth
    q_row = (await db.execute(_Q_QUEUE_DEPTH, {"store_id": store_id})).fetchone()
    queue_depth = int(q_row[0]) if q_row and q_row[0] is not None else 0

    # Abandonment rate
    ab_row = (await db.execute(_Q_ABANDONMENT, params)).fetchone()
    abandoned = int(ab_row[0]) if ab_row and ab_row[0] else 0
    joined    = int(ab_row[1]) if ab_row and ab_row[1] else 0
    abandonment_rate = round(abandoned / joined, 4) if joined > 0 else 0.0

    return StoreMetrics(
        store_id=store_id,
        date=date_str,
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        avg_dwell_per_zone=avg_dwell_per_zone,
        queue_depth_current=queue_depth,
        abandonment_rate=abandonment_rate,
    )


# ── Cache helpers ─────────────────────────────────────────────────────────────

async def _cache_get(key: str) -> Optional[str]:
    if not REDIS_AVAILABLE or _redis is None:
        return None
    try:
        return await _redis.get(key)
    except Exception:
        return None


async def _cache_set(key: str, value: str, ttl: int) -> None:
    if not REDIS_AVAILABLE or _redis is None:
        return
    try:
        await _redis.setex(key, ttl, value)
    except Exception:
        pass


async def invalidate_metrics_cache(store_id: str) -> None:
    """Called by ingestion to bust the cache after new events."""
    if not REDIS_AVAILABLE or _redis is None:
        return
    try:
        keys = await _redis.keys(f"metrics:{store_id}:*")
        if keys:
            await _redis.delete(*keys)
    except Exception:
        pass


def _parse_date(date_str: Optional[str]) -> date:
    if date_str is None:
        return datetime.now(timezone.utc).date()
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_str!r}")
