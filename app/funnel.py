"""
GET /stores/{store_id}/funnel

Conversion funnel: Entry → Zone Visit → Billing Queue → Purchase
- Session is the unit (not raw events)
- Re-entries do NOT double-count a visitor
- POS correlation: visitor in billing zone within 5 min before transaction
- All SQL is portable (works on PostgreSQL and SQLite)
"""

from __future__ import annotations
import json
from datetime import date, datetime, timezone
from typing import List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ConversionFunnel, FunnelStage

logger = structlog.get_logger()
router = APIRouter()


# ── SQL (portable — no AT TIME ZONE, no FILTER clause) ───────────────────────

# Count unique customer sessions (no staff, no double-count on re-entry)
_Q_ENTRY = text("""
    SELECT COUNT(DISTINCT visitor_id)
    FROM sessions
    WHERE store_id = :store_id
      AND DATE(entry_time) = :date
      AND NOT is_staff
""")

# Sessions that visited at least one zone
# zones_visited is stored as a JSON array string e.g. '["SKINCARE","MAKEUP"]'
# A non-empty array means length > 2 (at minimum '["X"]' is 5 chars)
_Q_ZONE_VISIT = text("""
    SELECT COUNT(DISTINCT visitor_id)
    FROM sessions
    WHERE store_id = :store_id
      AND DATE(entry_time) = :date
      AND NOT is_staff
      AND zones_visited IS NOT NULL
      AND zones_visited != '[]'
      AND LENGTH(zones_visited) > 2
""")

# Sessions that joined the billing queue
_Q_BILLING = text("""
    SELECT COUNT(DISTINCT visitor_id)
    FROM sessions s
    WHERE s.store_id = :store_id
      AND DATE(s.entry_time) = :date
      AND NOT s.is_staff
      AND EXISTS (
          SELECT 1 FROM events e
          WHERE e.visitor_id = s.visitor_id
            AND e.event_type = 'BILLING_QUEUE_JOIN'
            AND DATE(e.timestamp) = :date
      )
""")

# Sessions that completed a purchase (POS correlation via session_id)
_Q_PURCHASE = text("""
    SELECT COUNT(DISTINCT s.visitor_id)
    FROM sessions s
    JOIN pos_transactions p ON p.session_id = s.session_id
    WHERE s.store_id = :store_id
      AND DATE(s.entry_time) = :date
      AND NOT s.is_staff
""")


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/{store_id}/funnel", response_model=ConversionFunnel)
async def get_funnel(
    store_id: str,
    date_str: Optional[str] = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
) -> ConversionFunnel:
    """Conversion funnel with drop-off percentages."""
    target_date = _parse_date(date_str)
    params = {"store_id": store_id, "date": str(target_date)}

    entry    = _scalar(await db.execute(_Q_ENTRY,      params))
    zone     = _scalar(await db.execute(_Q_ZONE_VISIT, params))
    billing  = _scalar(await db.execute(_Q_BILLING,    params))
    purchase = _scalar(await db.execute(_Q_PURCHASE,   params))

    stages: List[FunnelStage] = [
        FunnelStage(stage="entry",         count=entry,    drop_off_pct=0.0),
        FunnelStage(stage="zone_visit",    count=zone,     drop_off_pct=_drop(entry,   zone)),
        FunnelStage(stage="billing_queue", count=billing,  drop_off_pct=_drop(zone,    billing)),
        FunnelStage(stage="purchase",      count=purchase, drop_off_pct=_drop(billing, purchase)),
    ]

    logger.info("funnel_computed", store_id=store_id, date=str(target_date),
                entry=entry, zone=zone, billing=billing, purchase=purchase)

    return ConversionFunnel(
        store_id=store_id,
        date=str(target_date),
        stages=stages,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _scalar(result) -> int:
    row = result.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _drop(prev: int, curr: int) -> float:
    if prev == 0:
        return 0.0
    return round((1 - curr / prev) * 100, 1)


def _parse_date(date_str: Optional[str]) -> date:
    if date_str is None:
        return datetime.now(timezone.utc).date()
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date_str!r}")
