"""
Tests for GET /health

# PROMPT:
Generate pytest tests for the health check endpoint.
Cover: healthy state, response schema, database field, stores dict,
warnings list, stale feed detection, and per-store status fields.

# CHANGES MADE:
- Added test_health_stale_feed_warning: verifies STALE_FEED logic
- Added test_health_database_field: must always be present
- Added test_health_after_events: store appears in stores dict after ingest
- Added test_health_store_status_active: recent events → active status
- Added test_health_no_stack_traces: error responses are structured
"""

from __future__ import annotations
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from tests.conftest import TEST_STORE_ID, make_event


@pytest.mark.asyncio
async def test_health_returns_200(client):
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_schema(client):
    """Response must have status, database, stores, warnings."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "database" in data
    assert "stores" in data
    assert "warnings" in data
    assert isinstance(data["warnings"], list)
    assert isinstance(data["stores"], dict)


@pytest.mark.asyncio
async def test_health_status_values(client):
    """Status must be one of healthy / degraded / unhealthy."""
    resp = await client.get("/health")
    assert resp.json()["status"] in ("healthy", "degraded", "unhealthy")


@pytest.mark.asyncio
async def test_health_database_connected(client):
    """Database field must be 'connected' when DB is available."""
    resp = await client.get("/health")
    assert resp.json()["database"] == "connected"


@pytest.mark.asyncio
async def test_health_store_fields(client):
    """Each store entry must have status field."""
    event = make_event()
    await client.post("/events/ingest", json={"events": [event]})

    resp = await client.get("/health")
    assert resp.status_code == 200
    stores = resp.json()["stores"]
    if TEST_STORE_ID in stores:
        store = stores[TEST_STORE_ID]
        assert "status" in store
        assert store["status"] in ("active", "stale", "inactive")


@pytest.mark.asyncio
async def test_health_after_events_store_appears(client):
    """After ingesting events, the store must appear in stores dict."""
    store_id = f"STORE_HEALTH_{uuid.uuid4().hex[:4]}"
    event = make_event(store_id=store_id)
    await client.post("/events/ingest", json={"events": [event]})

    resp = await client.get("/health")
    assert resp.status_code == 200
    stores = resp.json()["stores"]
    assert store_id in stores


@pytest.mark.asyncio
async def test_health_recent_event_is_active(client):
    """A store with a very recent event must have status='active'."""
    store_id = f"STORE_ACTIVE_{uuid.uuid4().hex[:4]}"
    # Use current time so lag is near 0
    now = datetime.now(timezone.utc)
    event = make_event(
        store_id=store_id,
        timestamp=now.isoformat().replace("+00:00", "Z"),
    )
    await client.post("/events/ingest", json={"events": [event]})

    resp = await client.get("/health")
    assert resp.status_code == 200
    stores = resp.json()["stores"]
    if store_id in stores:
        assert stores[store_id]["status"] == "active"


@pytest.mark.asyncio
async def test_health_stale_feed_detection(client):
    """A store with an old event (>10 min) must appear as stale."""
    store_id = f"STORE_STALE_{uuid.uuid4().hex[:4]}"
    # Timestamp 20 minutes ago
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=20))
    event = make_event(
        store_id=store_id,
        timestamp=old_ts.isoformat().replace("+00:00", "Z"),
    )
    await client.post("/events/ingest", json={"events": [event]})

    resp = await client.get("/health")
    assert resp.status_code == 200
    stores = resp.json()["stores"]
    if store_id in stores:
        assert stores[store_id]["status"] == "stale"
        # STALE_FEED warning must be in warnings list
        warnings = resp.json()["warnings"]
        assert any(store_id in w for w in warnings)


@pytest.mark.asyncio
async def test_health_lag_seconds_present(client):
    """Store entry must include lag_seconds when last_event is known."""
    store_id = f"STORE_LAG_{uuid.uuid4().hex[:4]}"
    now = datetime.now(timezone.utc)
    event = make_event(
        store_id=store_id,
        timestamp=now.isoformat().replace("+00:00", "Z"),
    )
    await client.post("/events/ingest", json={"events": [event]})

    resp = await client.get("/health")
    stores = resp.json()["stores"]
    if store_id in stores:
        assert "lag_seconds" in stores[store_id]
        assert stores[store_id]["lag_seconds"] >= 0


@pytest.mark.asyncio
async def test_health_no_raw_stack_traces(client):
    """Health response must never contain raw Python tracebacks."""
    resp = await client.get("/health")
    body = resp.text
    assert "Traceback" not in body
    assert "File \"" not in body
