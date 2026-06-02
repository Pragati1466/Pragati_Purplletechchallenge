"""
GET /stores/{store_id}/anomalies

Detects three anomaly types in real-time:
  1. BILLING_QUEUE_SPIKE  — queue depth > 2σ above 7-day rolling avg
  2. CONVERSION_DROP      — today's conversion rate < 7-day avg - 1σ
  3. DEAD_ZONE            — no visits to a zone in 30 min (during open hours)

Severity: INFO (1σ) / WARN (2σ) / CRITICAL (3σ or operational)

SQL is written to work on both PostgreSQL (production) and SQLite (tests).
PostgreSQL uses ->> operator for JSONB; SQLite uses json_extract().
We detect the dialect at runtime and use the appropriate expression.
"""

from __future__ import annotations
import math
import os
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Anomaly, AnomalySeverity, StoreAnomalies

logger = structlog.get_logger()
router = APIRouter()

# ── Dialect detection ─────────────────────────────────────────────────────────
# Check DATABASE_URL to decide which JSON extraction syntax to use.
_DB_URL = os.getenv("DATABASE_URL", "sqlite")
_IS_POSTGRES = "postgresql" in _DB_URL or "postgres" in _DB_URL

# PostgreSQL: metadata->>'queue_depth'   (JSONB operator)
# SQLite:     json_extract(metadata, '$.queue_depth')
_JSON_QUEUE_DEPTH = (
    "(metadata->>'queue_depth')::FLOAT"
    if _IS_POSTGRES
    else "CAST(json_extract(metadata, '$.queue_depth') AS FLOAT)"
)


# ── SQL ───────────────────────────────────────────────────────────────────────

def _q_queue_history() -> text:
    return text(f"""
        SELECT
            DATE(timestamp) AS day,
            AVG({_JSON_QUEUE_DEPTH}) AS avg_depth
        FROM events
        WHERE store_id = :store_id
          AND event_type = 'BILLING_QUEUE_JOIN'
          AND NOT is_staff
          AND timestamp >= :since
        GROUP BY day
        ORDER BY day
    """)


def _q_queue_current() -> text:
    return text(f"""
        SELECT {_JSON_QUEUE_DEPTH}
        FROM events
        WHERE store_id = :store_id
          AND event_type = 'BILLING_QUEUE_JOIN'
          AND NOT is_staff
        ORDER BY timestamp DESC
        LIMIT 1
    """)


_Q_CONVERSION_HISTORY = text("""
    SELECT
        DATE(s.entry_time) AS day,
        CAST(COUNT(DISTINCT CASE WHEN p.session_id IS NOT NULL THEN s.visitor_id END) AS FLOAT)
            / NULLIF(COUNT(DISTINCT s.visitor_id), 0) AS conv_rate
    FROM sessions s
    LEFT JOIN pos_transactions p ON p.session_id = s.session_id
    WHERE s.store_id = :store_id
      AND NOT s.is_staff
      AND s.entry_time >= :since
    GROUP BY day
    ORDER BY day
""")

_Q_CONVERSION_TODAY = text("""
    SELECT
        CAST(COUNT(DISTINCT CASE WHEN p.session_id IS NOT NULL THEN s.visitor_id END) AS FLOAT)
            / NULLIF(COUNT(DISTINCT s.visitor_id), 0)
    FROM sessions s
    LEFT JOIN pos_transactions p ON p.session_id = s.session_id
    WHERE s.store_id = :store_id
      AND NOT s.is_staff
      AND DATE(s.entry_time) = :today
""")

_Q_ZONE_LAST_VISIT = text("""
    SELECT zone_id, MAX(timestamp) AS last_visit
    FROM events
    WHERE store_id = :store_id
      AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
      AND NOT is_staff
      AND zone_id IS NOT NULL
      AND timestamp >= :since
    GROUP BY zone_id
""")

# Derive known zones from events — portable, no unnest/JSONB
_Q_ALL_ZONES_FROM_EVENTS = text("""
    SELECT DISTINCT zone_id
    FROM events
    WHERE store_id = :store_id
      AND zone_id IS NOT NULL
      AND zone_id NOT IN ('ENTRY', 'EXIT', 'ENTRY_EXIT', 'BILLING')
""")


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/{store_id}/anomalies", response_model=StoreAnomalies)
async def get_anomalies(
    store_id: str,
    db: AsyncSession = Depends(get_db),
) -> StoreAnomalies:
    now = datetime.now(timezone.utc)
    anomalies: List[Anomaly] = []

    anomalies += await _check_queue_spike(store_id, db, now)
    anomalies += await _check_conversion_drop(store_id, db, now)
    anomalies += await _check_dead_zones(store_id, db, now)

    logger.info("anomalies_computed", store_id=store_id, count=len(anomalies))
    return StoreAnomalies(store_id=store_id, anomalies=anomalies)


# ── Anomaly detectors ─────────────────────────────────────────────────────────

async def _check_queue_spike(
    store_id: str, db: AsyncSession, now: datetime
) -> List[Anomaly]:
    since = (now - timedelta(days=7)).isoformat()
    rows = (await db.execute(
        _q_queue_history(), {"store_id": store_id, "since": since}
    )).fetchall()
    if len(rows) < 2:
        return []

    values = [float(r[1]) for r in rows if r[1] is not None]
    if not values:
        return []

    mean, std = _stats(values)
    current_row = (await db.execute(
        _q_queue_current(), {"store_id": store_id}
    )).fetchone()
    current = float(current_row[0]) if current_row and current_row[0] is not None else 0.0

    deviations = (current - mean) / (std + 1e-9)
    severity = _severity(deviations)
    if severity is None:
        return []

    return [Anomaly(
        type="BILLING_QUEUE_SPIKE",
        severity=severity,
        detected_at=now,
        current_value=current,
        baseline_value=round(mean, 2),
        suggested_action=(
            "Deploy additional billing counter staff immediately"
            if severity == AnomalySeverity.CRITICAL
            else "Monitor billing queue — consider opening another counter"
        ),
    )]


async def _check_conversion_drop(
    store_id: str, db: AsyncSession, now: datetime
) -> List[Anomaly]:
    since = (now - timedelta(days=7)).isoformat()
    today = now.date().isoformat()
    rows = (await db.execute(
        _Q_CONVERSION_HISTORY, {"store_id": store_id, "since": since}
    )).fetchall()
    if len(rows) < 2:
        return []

    values = [float(r[1]) for r in rows if r[1] is not None]
    if not values:
        return []

    mean, std = _stats(values)
    today_row = (await db.execute(
        _Q_CONVERSION_TODAY, {"store_id": store_id, "today": today}
    )).fetchone()
    today_rate = float(today_row[0]) if today_row and today_row[0] is not None else 0.0

    deviations = (mean - today_rate) / (std + 1e-9)
    severity = _severity(deviations)
    if severity is None:
        return []

    return [Anomaly(
        type="CONVERSION_DROP",
        severity=severity,
        detected_at=now,
        current_value=round(today_rate, 4),
        baseline_value=round(mean, 4),
        suggested_action=(
            "Urgent: review product availability, pricing, and staff engagement"
            if severity == AnomalySeverity.CRITICAL
            else "Review product placement and promotions in low-conversion zones"
        ),
    )]


async def _check_dead_zones(
    store_id: str, db: AsyncSession, now: datetime
) -> List[Anomaly]:
    """Flag zones with no visits in the last 30 minutes."""
    since = (now - timedelta(hours=2)).isoformat()
    last_visit_rows = (await db.execute(
        _Q_ZONE_LAST_VISIT, {"store_id": store_id, "since": since}
    )).fetchall()

    last_visit_map: dict = {}
    for r in last_visit_rows:
        raw_ts = r[1]
        if raw_ts is None:
            continue
        if isinstance(raw_ts, str):
            try:
                raw_ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            except ValueError:
                continue
        if raw_ts.tzinfo is None:
            raw_ts = raw_ts.replace(tzinfo=timezone.utc)
        last_visit_map[r[0]] = raw_ts

    zone_rows = (await db.execute(
        _Q_ALL_ZONES_FROM_EVENTS, {"store_id": store_id}
    )).fetchall()
    all_zones = [r[0] for r in zone_rows if r[0]]

    anomalies = []
    threshold = now - timedelta(minutes=30)

    for zone_id in all_zones:
        last = last_visit_map.get(zone_id)
        if last is None or last < threshold:
            anomalies.append(Anomaly(
                type="DEAD_ZONE",
                severity=AnomalySeverity.INFO,
                detected_at=now,
                current_value=0.0,
                baseline_value=1.0,
                suggested_action=(
                    f"Check lighting and product placement in {zone_id} zone — "
                    "no customer visits in 30+ minutes"
                ),
            ))

    return anomalies


# ── Statistics helpers ────────────────────────────────────────────────────────

def _stats(values: List[float]):
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    std = math.sqrt(variance)
    return mean, std


def _severity(deviations: float) -> Optional[AnomalySeverity]:
    if deviations >= 3.0:
        return AnomalySeverity.CRITICAL
    if deviations >= 2.0:
        return AnomalySeverity.WARN
    if deviations >= 1.0:
        return AnomalySeverity.INFO
    return None
