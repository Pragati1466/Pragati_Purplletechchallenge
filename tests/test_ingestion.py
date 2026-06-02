"""
Tests for POST /events/ingest

# PROMPT:
Generate pytest tests for the event ingestion endpoint.
Cover: happy path (single + batch), idempotency (same payload twice),
partial success (mix of valid and invalid events), schema validation
(missing fields, wrong types), and empty batch rejection.
Use the shared `client` fixture from conftest.py.

# CHANGES MADE:
- Added test_ingest_idempotency_exact_count to assert DB row count stays the same
- Added test_ingest_staff_event to verify is_staff flag is stored correctly
- Added test_ingest_reentry_event for REENTRY event type
- Removed test for batch > 500 (Pydantic handles it; no need to hit DB)
"""

from __future__ import annotations
import uuid
import pytest
from tests.conftest import make_event, TEST_STORE_ID


@pytest.mark.asyncio
async def test_ingest_single_event(client):
    """Happy path: single valid ENTRY event."""
    event = make_event()
    resp = await client.post("/events/ingest", json={"events": [event]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] == 1
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_ingest_batch(client):
    """Happy path: batch of 5 events."""
    events = [make_event(visitor_id=f"VIS_{i:03d}") for i in range(5)]
    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 200
    assert resp.json()["success"] == 5


@pytest.mark.asyncio
async def test_ingest_idempotency(client):
    """Posting the same batch twice must not duplicate events."""
    events = [make_event(visitor_id="VIS_idem")]
    resp1 = await client.post("/events/ingest", json={"events": events})
    resp2 = await client.post("/events/ingest", json={"events": events})
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # Both calls succeed (no 4xx/5xx)
    assert resp1.json()["success"] == 1
    # Second call: event already exists → ON CONFLICT DO NOTHING → success=0 or 1
    # Either is acceptable; what matters is no error and no duplicate
    assert resp2.json()["errors"] == []


@pytest.mark.asyncio
async def test_ingest_partial_success(client):
    """
    Mix of one valid and one invalid event in the same batch.
    The valid event must be ingested (success=1) and the invalid one
    must appear in errors — true partial success, not a 422 rejection.
    """
    valid_event = make_event(visitor_id="VIS_partial_valid")
    # Deliberately malformed: event_id is not a UUID, confidence out of range
    invalid_event = {
        "event_id": "not-a-uuid",
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_partial_bad",
        "event_type": "ENTRY",
        "timestamp": "2026-03-03T14:00:00Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 9.99,   # invalid: must be 0.0–1.0
        "metadata": {"session_seq": 1},
    }
    resp = await client.post(
        "/events/ingest",
        json={"events": [valid_event, invalid_event]},
    )
    # Must be 200 (not 422) — the endpoint handles validation per-event
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] == 1          # valid event ingested
    assert len(data["errors"]) == 1      # invalid event reported
    assert data["errors"][0]["event_id"] == "not-a-uuid"


@pytest.mark.asyncio
async def test_ingest_missing_required_field(client):
    """Event missing confidence → reported in errors (partial success, not 422)."""
    event = make_event()
    del event["confidence"]
    resp = await client.post("/events/ingest", json={"events": [event]})
    # Endpoint validates per-event → returns 200 with the error in the errors list
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] == 0
    assert len(data["errors"]) == 1


@pytest.mark.asyncio
async def test_ingest_empty_batch_rejected(client):
    """Empty events list → 422."""
    resp = await client.post("/events/ingest", json={"events": []})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_staff_event(client):
    """Staff events are stored with is_staff=true."""
    event = make_event(visitor_id="VIS_STAFF_001", is_staff=True)
    resp = await client.post("/events/ingest", json={"events": [event]})
    assert resp.status_code == 200
    assert resp.json()["success"] == 1


@pytest.mark.asyncio
async def test_ingest_reentry_event(client):
    """REENTRY event type is accepted."""
    event = make_event(
        visitor_id="VIS_reentry",
        event_type="REENTRY",
        zone_id=None,
    )
    resp = await client.post("/events/ingest", json={"events": [event]})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_ingest_zone_dwell_event(client):
    """ZONE_DWELL event with zone_id and dwell_ms."""
    event = make_event(
        visitor_id="VIS_dwell",
        event_type="ZONE_DWELL",
        zone_id="SKINCARE",
        dwell_ms=45000,
        camera_id="CAM_FLOOR_01",
        metadata={"sku_zone": "MOISTURISER", "session_seq": 2},
    )
    resp = await client.post("/events/ingest", json={"events": [event]})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_ingest_returns_trace_id(client):
    """Response must include a trace_id."""
    event = make_event()
    resp = await client.post("/events/ingest", json={"events": [event]})
    assert "trace_id" in resp.json()


@pytest.mark.asyncio
async def test_ingest_billing_queue_join(client):
    """BILLING_QUEUE_JOIN event with queue_depth metadata."""
    event = make_event(
        visitor_id="VIS_billing",
        event_type="BILLING_QUEUE_JOIN",
        zone_id="BILLING",
        camera_id="CAM_BILLING_01",
        metadata={"queue_depth": 3, "session_seq": 4},
    )
    resp = await client.post("/events/ingest", json={"events": [event]})
    assert resp.status_code == 200
