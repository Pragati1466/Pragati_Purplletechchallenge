"""
GET /health

Returns service status, last event timestamp per store,
and STALE_FEED warning if any store has > 10 min lag.

Structured log fields per spec:
  trace_id, store_id, endpoint, latency_ms, status_code
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import HealthResponse, StoreHealth

logger = structlog.get_logger()
router = APIRouter()

STALE_THRESHOLD_SECONDS = 600  # 10 minutes

_Q_LAST_EVENTS = text("""
    SELECT store_id, MAX(timestamp) AS last_event
    FROM events
    GROUP BY store_id
""")

_Q_DB_PING = text("SELECT 1")


@router.get("/health", response_model=HealthResponse)
async def health_check(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HealthResponse:
    now = datetime.now(timezone.utc)
    warnings: List[str] = []
    trace_id = getattr(request.state, "trace_id", "unknown")

    # ── Database check ────────────────────────────────────────────────────────
    db_status = "connected"
    try:
        await db.execute(_Q_DB_PING)
    except Exception as exc:
        db_status = f"error: {exc}"
        logger.error("health_db_error", trace_id=trace_id, error=str(exc))

    # ── Per-store feed status ─────────────────────────────────────────────────
    stores: Dict[str, StoreHealth] = {}
    try:
        rows = (await db.execute(_Q_LAST_EVENTS)).fetchall()
        for row in rows:
            store_id, last_event = row[0], row[1]
            if last_event is None:
                stores[store_id] = StoreHealth(
                    last_event=None, lag_seconds=None, status="inactive"
                )
                continue

            # Normalise to timezone-aware datetime
            if isinstance(last_event, str):
                try:
                    last_event = datetime.fromisoformat(
                        last_event.replace("Z", "+00:00")
                    )
                except ValueError:
                    stores[store_id] = StoreHealth(
                        last_event=None, lag_seconds=None, status="inactive"
                    )
                    continue

            if last_event.tzinfo is None:
                last_event = last_event.replace(tzinfo=timezone.utc)

            lag = int((now - last_event).total_seconds())
            if lag > STALE_THRESHOLD_SECONDS:
                status = "stale"
                warnings.append(f"STALE_FEED:{store_id} (lag {lag}s)")
            else:
                status = "active"

            stores[store_id] = StoreHealth(
                last_event=last_event,
                lag_seconds=lag,
                status=status,
            )
    except Exception as exc:
        logger.error("health_store_query_error", trace_id=trace_id, error=str(exc))

    # ── Overall status ────────────────────────────────────────────────────────
    if db_status != "connected":
        overall = "unhealthy"
    elif any(s.status == "stale" for s in stores.values()):
        overall = "degraded"
    else:
        overall = "healthy"

    # Structured log — includes store_id summary and endpoint
    logger.info(
        "health_check",
        trace_id=trace_id,
        endpoint="/health",
        overall=overall,
        db=db_status,
        store_count=len(stores),
        stale_stores=[sid for sid, s in stores.items() if s.status == "stale"],
        warnings=warnings,
    )

    return HealthResponse(
        status=overall,
        database=db_status,
        stores=stores,
        warnings=warnings,
    )
