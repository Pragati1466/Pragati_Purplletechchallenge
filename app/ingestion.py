"""
POST /events/ingest

Accepts batches of up to 500 events.
- Validates each event against EventSchema (Pydantic)
- Deduplicates by event_id (idempotent — safe to call twice)
- Returns partial success: {success, errors}
- Structured logging per request
"""

from __future__ import annotations
import json
import uuid
from typing import Any, Dict, List

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import EventSchema, IngestResponse

logger = structlog.get_logger()
router = APIRouter()


# ── SQL ───────────────────────────────────────────────────────────────────────

# Metadata stored as plain TEXT (works on both PostgreSQL and SQLite)
_INSERT_EVENT = text("""
    INSERT INTO events (
        event_id, store_id, camera_id, visitor_id, event_type,
        timestamp, zone_id, dwell_ms, is_staff, confidence, metadata
    ) VALUES (
        :event_id, :store_id, :camera_id, :visitor_id, :event_type,
        :timestamp, :zone_id, :dwell_ms, :is_staff, :confidence, :metadata
    )
    ON CONFLICT (event_id) DO NOTHING
""")

# Portable upsert — no array_append, no JSONB operators, no NOW()
# Zone tracking is handled separately in Python via _update_session_zones()
_UPSERT_SESSION = text("""
    INSERT INTO sessions (session_id, store_id, visitor_id, entry_time, is_staff)
    VALUES (:session_id, :store_id, :visitor_id, :entry_time, :is_staff)
    ON CONFLICT (visitor_id) DO UPDATE SET
        exit_time = CASE
            WHEN :event_type = 'EXIT' THEN :event_time
            ELSE sessions.exit_time
        END,
        total_dwell_ms = sessions.total_dwell_ms + :dwell_ms,
        reentry_count = CASE
            WHEN :event_type = 'REENTRY' THEN sessions.reentry_count + 1
            ELSE sessions.reentry_count
        END,
        updated_at = :event_time
""")

# Fetch current zones_visited for a visitor (portable)
_GET_ZONES = text("""
    SELECT zones_visited FROM sessions WHERE visitor_id = :visitor_id
""")

# Update zones_visited as a JSON string (portable — works on SQLite and PostgreSQL)
_SET_ZONES = text("""
    UPDATE sessions SET zones_visited = :zones WHERE visitor_id = :visitor_id
""")


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/ingest", response_model=IngestResponse)
async def ingest_events(
    request: Request,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> IngestResponse:
    """
    Ingest a batch of events (up to 500).

    Accepts a raw JSON body ``{"events": [...]}`` and validates each event
    individually so that one malformed event does NOT reject the whole batch
    (true partial success).

    Idempotent: duplicate event_ids are silently ignored.
    """
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    success_count = 0
    errors: List[Dict[str, Any]] = []

    raw_events: List[Any] = payload.get("events", [])

    # Guard: must be a list
    if not isinstance(raw_events, list):
        return JSONResponse(
            status_code=422,
            content={"error": "validation_error", "message": "'events' must be a list"},
        )

    # Guard: batch size limit
    if len(raw_events) == 0:
        return JSONResponse(
            status_code=422,
            content={"error": "validation_error", "message": "'events' list must not be empty"},
        )
    if len(raw_events) > 500:
        return JSONResponse(
            status_code=422,
            content={"error": "validation_error", "message": "Batch size exceeds 500 events"},
        )

    log = logger.bind(trace_id=trace_id, event_count=len(raw_events))
    # Expose event_count to the logging middleware
    request.state.event_count = len(raw_events)

    for raw in raw_events:
        event_id_hint = raw.get("event_id", "<unknown>") if isinstance(raw, dict) else "<unknown>"
        try:
            # Per-event Pydantic validation — one bad event doesn't kill the batch
            event = EventSchema.model_validate(raw)
            await _insert_event(db, event)
            success_count += 1
        except ValidationError as exc:
            errors.append({
                "event_id": event_id_hint,
                "error": "schema_validation_failed",
                "detail": exc.errors(),
            })
            log.warning("event_validation_error", event_id=event_id_hint)
        except Exception as exc:
            errors.append({
                "event_id": event_id_hint,
                "error": str(exc),
            })
            log.warning("event_ingest_error", event_id=event_id_hint, error=str(exc))

    log.info("events_ingested", success=success_count, errors=len(errors))

    return IngestResponse(
        success=success_count,
        errors=errors,
        trace_id=trace_id,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _insert_event(db: AsyncSession, event: EventSchema) -> None:
    # Serialize metadata as plain JSON string (works on both PG and SQLite)
    metadata_str = json.dumps(event.metadata)

    await db.execute(_INSERT_EVENT, {
        "event_id":   str(event.event_id),
        "store_id":   event.store_id,
        "camera_id":  event.camera_id,
        "visitor_id": event.visitor_id,
        "event_type": event.event_type.value,
        "timestamp":  event.timestamp.isoformat(),
        "zone_id":    event.zone_id,
        "dwell_ms":   event.dwell_ms,
        "is_staff":   event.is_staff,
        "confidence": event.confidence,
        "metadata":   metadata_str,
    })

    # Keep sessions table in sync (portable SQL — no PG-specific syntax)
    await db.execute(_UPSERT_SESSION, {
        "session_id": str(uuid.uuid4()),
        "store_id":   event.store_id,
        "visitor_id": event.visitor_id,
        "entry_time": event.timestamp.isoformat(),
        "is_staff":   event.is_staff,
        "event_type": event.event_type.value,
        "event_time": event.timestamp.isoformat(),
        "dwell_ms":   event.dwell_ms,
    })

    # Update zones_visited in Python (avoids array_append / JSONB — works on SQLite too)
    if event.zone_id and event.event_type.value == "ZONE_ENTER":
        await _update_session_zones(db, event.visitor_id, event.zone_id)


async def _update_session_zones(
    db: AsyncSession, visitor_id: str, zone_id: str
) -> None:
    """Append zone_id to sessions.zones_visited (stored as JSON array string)."""
    row = (await db.execute(_GET_ZONES, {"visitor_id": visitor_id})).fetchone()
    if row is None:
        return

    raw = row[0]
    try:
        zones: list = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        zones = []

    if zone_id not in zones:
        zones.append(zone_id)

    await db.execute(_SET_ZONES, {
        "zones": json.dumps(zones),
        "visitor_id": visitor_id,
    })
