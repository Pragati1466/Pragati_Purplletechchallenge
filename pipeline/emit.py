"""
Event Emitter - converts tracking state into structured EventSchema objects.

All events are written to a JSONL file and optionally POSTed to the API.
"""

from __future__ import annotations
import json
import uuid
import httpx
from datetime import datetime, timezone
from typing import Optional, List, IO
from pathlib import Path

# We import the Pydantic model for validation before writing
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.models import EventSchema, EventType


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _make_event(
    store_id: str,
    camera_id: str,
    visitor_id: str,
    event_type: EventType,
    timestamp: datetime,
    zone_id: Optional[str],
    dwell_ms: int,
    is_staff: bool,
    confidence: float,
    metadata: dict,
) -> EventSchema:
    return EventSchema(
        event_id=uuid.uuid4(),
        store_id=store_id,
        camera_id=camera_id,
        visitor_id=visitor_id,
        event_type=event_type,
        timestamp=timestamp,
        zone_id=zone_id,
        dwell_ms=dwell_ms,
        is_staff=is_staff,
        confidence=confidence,
        metadata=metadata,
    )


class EventEmitter:
    """
    Writes events to a JSONL file and optionally streams them to the API.

    Args:
        output_path: Path to output .jsonl file.
        api_url:     If set, POST batches to {api_url}/events/ingest.
        batch_size:  Number of events to buffer before flushing to API.
    """

    def __init__(
        self,
        output_path: str,
        api_url: Optional[str] = None,
        batch_size: int = 50,
    ):
        self._output_path = Path(output_path)
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._file: IO = open(self._output_path, "a", encoding="utf-8")
        self._api_url = api_url
        self._batch_size = batch_size
        self._buffer: List[EventSchema] = []
        self._session_seq: dict = {}   # visitor_id -> int

    # ------------------------------------------------------------------
    # Public emit methods (one per event type)
    # ------------------------------------------------------------------

    def emit_entry(
        self,
        store_id: str,
        camera_id: str,
        visitor_id: str,
        timestamp: datetime,
        is_staff: bool,
        confidence: float,
    ) -> EventSchema:
        self._session_seq[visitor_id] = 1
        return self._emit(
            store_id, camera_id, visitor_id, EventType.ENTRY,
            timestamp, None, 0, is_staff, confidence,
            {"session_seq": 1},
        )

    def emit_exit(
        self,
        store_id: str,
        camera_id: str,
        visitor_id: str,
        timestamp: datetime,
        is_staff: bool,
        confidence: float,
    ) -> EventSchema:
        seq = self._next_seq(visitor_id)
        return self._emit(
            store_id, camera_id, visitor_id, EventType.EXIT,
            timestamp, None, 0, is_staff, confidence,
            {"session_seq": seq},
        )

    def emit_reentry(
        self,
        store_id: str,
        camera_id: str,
        visitor_id: str,
        timestamp: datetime,
        is_staff: bool,
        confidence: float,
    ) -> EventSchema:
        seq = self._next_seq(visitor_id)
        return self._emit(
            store_id, camera_id, visitor_id, EventType.REENTRY,
            timestamp, None, 0, is_staff, confidence,
            {"session_seq": seq},
        )

    def emit_zone_enter(
        self,
        store_id: str,
        camera_id: str,
        visitor_id: str,
        zone_id: str,
        timestamp: datetime,
        is_staff: bool,
        confidence: float,
        sku_zone: Optional[str] = None,
    ) -> EventSchema:
        seq = self._next_seq(visitor_id)
        return self._emit(
            store_id, camera_id, visitor_id, EventType.ZONE_ENTER,
            timestamp, zone_id, 0, is_staff, confidence,
            {"sku_zone": sku_zone or zone_id, "session_seq": seq},
        )

    def emit_zone_exit(
        self,
        store_id: str,
        camera_id: str,
        visitor_id: str,
        zone_id: str,
        timestamp: datetime,
        dwell_ms: int,
        is_staff: bool,
        confidence: float,
    ) -> EventSchema:
        seq = self._next_seq(visitor_id)
        return self._emit(
            store_id, camera_id, visitor_id, EventType.ZONE_EXIT,
            timestamp, zone_id, dwell_ms, is_staff, confidence,
            {"session_seq": seq},
        )

    def emit_zone_dwell(
        self,
        store_id: str,
        camera_id: str,
        visitor_id: str,
        zone_id: str,
        timestamp: datetime,
        dwell_ms: int,
        is_staff: bool,
        confidence: float,
        sku_zone: Optional[str] = None,
    ) -> EventSchema:
        seq = self._next_seq(visitor_id)
        return self._emit(
            store_id, camera_id, visitor_id, EventType.ZONE_DWELL,
            timestamp, zone_id, dwell_ms, is_staff, confidence,
            {"sku_zone": sku_zone or zone_id, "session_seq": seq},
        )

    def emit_billing_queue_join(
        self,
        store_id: str,
        camera_id: str,
        visitor_id: str,
        timestamp: datetime,
        queue_depth: int,
        is_staff: bool,
        confidence: float,
    ) -> EventSchema:
        seq = self._next_seq(visitor_id)
        return self._emit(
            store_id, camera_id, visitor_id, EventType.BILLING_QUEUE_JOIN,
            timestamp, "BILLING", 0, is_staff, confidence,
            {"queue_depth": queue_depth, "session_seq": seq},
        )

    def emit_billing_queue_abandon(
        self,
        store_id: str,
        camera_id: str,
        visitor_id: str,
        timestamp: datetime,
        is_staff: bool,
        confidence: float,
    ) -> EventSchema:
        seq = self._next_seq(visitor_id)
        return self._emit(
            store_id, camera_id, visitor_id, EventType.BILLING_QUEUE_ABANDON,
            timestamp, "BILLING", 0, is_staff, confidence,
            {"session_seq": seq},
        )

    def flush(self) -> None:
        """Force-flush buffered events to API."""
        if self._buffer and self._api_url:
            self._post_batch(self._buffer)
        self._buffer.clear()
        self._file.flush()

    def close(self) -> None:
        self.flush()
        self._file.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _emit(
        self,
        store_id, camera_id, visitor_id, event_type,
        timestamp, zone_id, dwell_ms, is_staff, confidence, metadata,
    ) -> EventSchema:
        event = _make_event(
            store_id, camera_id, visitor_id, event_type,
            timestamp, zone_id, dwell_ms, is_staff, confidence, metadata,
        )
        # Write to JSONL
        line = event.model_dump_json() + "\n"
        self._file.write(line)

        # Buffer for API
        self._buffer.append(event)
        if len(self._buffer) >= self._batch_size:
            self.flush()

        return event

    def _next_seq(self, visitor_id: str) -> int:
        seq = self._session_seq.get(visitor_id, 0) + 1
        self._session_seq[visitor_id] = seq
        return seq

    def _post_batch(self, events: List[EventSchema]) -> None:
        payload = {"events": [e.model_dump(mode="json") for e in events]}
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(f"{self._api_url}/events/ingest", json=payload)
                resp.raise_for_status()
        except Exception as exc:
            print(f"[emit] WARNING: failed to POST batch to API: {exc}")
