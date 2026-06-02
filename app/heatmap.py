"""
GET /stores/{store_id}/heatmap

Zone visit frequency + avg dwell, normalised 0-100.
Includes data_confidence=false if fewer than 20 sessions in window.
All SQL is portable (works on PostgreSQL and SQLite).
"""

from __future__ import annotations
from datetime import date, datetime, timezone
from typing import List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import StoreHeatmap, ZoneHeatmapData

logger = structlog.get_logger()
router = APIRouter()

_Q_ZONE_STATS = text("""
    SELECT
        zone_id,
        COUNT(*)                              AS visit_count,
        CAST(COALESCE(AVG(dwell_ms), 0) AS INTEGER) AS avg_dwell_ms
    FROM events
    WHERE store_id = :store_id
      AND DATE(timestamp) = :date
      AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
      AND NOT is_staff
      AND zone_id IS NOT NULL
    GROUP BY zone_id
    ORDER BY visit_count DESC
""")

_Q_SESSION_COUNT = text("""
    SELECT COUNT(DISTINCT visitor_id)
    FROM sessions
    WHERE store_id = :store_id
      AND DATE(entry_time) = :date
      AND NOT is_staff
""")


@router.get("/{store_id}/heatmap", response_model=StoreHeatmap)
async def get_heatmap(
    store_id: str,
    date_str: Optional[str] = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
) -> StoreHeatmap:
    target_date = _parse_date(date_str)
    params = {"store_id": store_id, "date": str(target_date)}

    rows = (await db.execute(_Q_ZONE_STATS, params)).fetchall()
    session_count = _scalar(await db.execute(_Q_SESSION_COUNT, params))
    data_confidence = session_count >= 20

    zones: List[ZoneHeatmapData] = []
    if rows:
        max_visits = max(int(r[1]) for r in rows)
        max_dwell  = max(int(r[2]) for r in rows) or 1

        for row in rows:
            zone_id, visit_count, avg_dwell = row[0], int(row[1]), int(row[2])
            visit_score = (visit_count / max_visits) * 50
            dwell_score = (avg_dwell  / max_dwell)  * 50
            normalized  = min(100, int(visit_score + dwell_score))
            zones.append(ZoneHeatmapData(
                zone_id=zone_id,
                visit_frequency=visit_count,
                avg_dwell_ms=avg_dwell,
                normalized_score=normalized,
            ))

    logger.info("heatmap_computed", store_id=store_id, date=str(target_date),
                zones=len(zones), data_confidence=data_confidence)

    return StoreHeatmap(
        store_id=store_id,
        date=str(target_date),
        zones=zones,
        data_confidence=data_confidence,
    )


def _scalar(result) -> int:
    row = result.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _parse_date(date_str: Optional[str]) -> date:
    if date_str is None:
        return datetime.now(timezone.utc).date()
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date_str!r}")
