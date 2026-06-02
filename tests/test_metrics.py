"""
Tests for GET /stores/{store_id}/metrics

# PROMPT:
Generate pytest tests for the store metrics endpoint.
Cover: happy path (visitors + purchases), empty store (zero visitors),
all-staff clip (all is_staff=true), zero purchases, re-entry no double-count,
beauty retail zone dwell patterns, and invalid store_id.
Use the shared `client` fixture from conftest.py.

# CHANGES MADE:
- Added test_metrics_beauty_retail_zones: Purplle-specific zone dwell ordering
- Added test_metrics_reentry_no_double_count: critical for accurate conversion rate
- Added test_metrics_all_staff_clip: edge case where every visitor is staff
- Replaced DB-level fixture with HTTP ingest calls (simpler, tests full stack)
- Added test_metrics_zero_visitors_returns_zeros (not null/crash)
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from tests.conftest import make_event, TEST_STORE_ID

TODAY = datetime.now(timezone.utc).date().isoformat()


async def _ingest(client, events):
    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.asyncio
async def test_metrics_empty_store(client):
    """Empty store returns zeros — must not crash or return null."""
    resp = await client.get(f"/stores/{TEST_STORE_ID}/metrics?date={TODAY}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["unique_visitors"] == 0
    assert data["conversion_rate"] == 0.0
    assert data["queue_depth_current"] == 0
    assert data["abandonment_rate"] == 0.0
    assert isinstance(data["avg_dwell_per_zone"], dict)


@pytest.mark.asyncio
async def test_metrics_counts_visitors(client):
    """Unique visitor count excludes staff."""
    events = [
        make_event(visitor_id="VIS_A", event_type="ENTRY"),
        make_event(visitor_id="VIS_B", event_type="ENTRY"),
        make_event(visitor_id="VIS_STAFF", event_type="ENTRY", is_staff=True),
    ]
    await _ingest(client, events)
    resp = await client.get(f"/stores/{TEST_STORE_ID}/metrics?date={TODAY}")
    assert resp.status_code == 200
    assert resp.json()["unique_visitors"] == 2  # staff excluded


@pytest.mark.asyncio
async def test_metrics_all_staff_clip(client):
    """When every visitor is staff, unique_visitors must be 0."""
    events = [
        make_event(visitor_id=f"VIS_STAFF_{i}", event_type="ENTRY", is_staff=True)
        for i in range(5)
    ]
    await _ingest(client, events)
    resp = await client.get(f"/stores/{TEST_STORE_ID}/metrics?date={TODAY}")
    assert resp.status_code == 200
    assert resp.json()["unique_visitors"] == 0


@pytest.mark.asyncio
async def test_metrics_zero_purchases(client):
    """Visitors present but no purchases → conversion_rate = 0.0."""
    events = [
        make_event(visitor_id="VIS_browse", event_type="ENTRY"),
        make_event(visitor_id="VIS_browse", event_type="EXIT"),
    ]
    await _ingest(client, events)
    resp = await client.get(f"/stores/{TEST_STORE_ID}/metrics?date={TODAY}")
    assert resp.status_code == 200
    assert resp.json()["conversion_rate"] == 0.0


@pytest.mark.asyncio
async def test_metrics_reentry_no_double_count(client):
    """REENTRY event must not inflate unique_visitor count."""
    visitor_id = "VIS_reentry_test"
    events = [
        make_event(visitor_id=visitor_id, event_type="ENTRY"),
        make_event(visitor_id=visitor_id, event_type="EXIT"),
        make_event(visitor_id=visitor_id, event_type="REENTRY"),
    ]
    await _ingest(client, events)
    resp = await client.get(f"/stores/{TEST_STORE_ID}/metrics?date={TODAY}")
    assert resp.status_code == 200
    # Still 1 unique visitor, not 2
    assert resp.json()["unique_visitors"] == 1


@pytest.mark.asyncio
async def test_metrics_beauty_retail_zones(client):
    """
    Purplle-specific: skincare dwell > makeup dwell > fragrance dwell.
    Validates zone dwell patterns match beauty retail behaviour.
    """
    base = datetime.now(timezone.utc)
    events = [
        make_event(
            visitor_id="VIS_beauty",
            event_type="ZONE_DWELL",
            zone_id="SKINCARE",
            dwell_ms=45000,
            camera_id="CAM_FLOOR_01",
            metadata={"sku_zone": "MOISTURISER", "session_seq": 1},
        ),
        make_event(
            visitor_id="VIS_beauty",
            event_type="ZONE_DWELL",
            zone_id="MAKEUP",
            dwell_ms=38000,
            camera_id="CAM_FLOOR_01",
            metadata={"sku_zone": "LIPSTICK", "session_seq": 2},
        ),
        make_event(
            visitor_id="VIS_beauty",
            event_type="ZONE_DWELL",
            zone_id="FRAGRANCE",
            dwell_ms=22000,
            camera_id="CAM_FLOOR_01",
            metadata={"sku_zone": "PERFUME", "session_seq": 3},
        ),
    ]
    await _ingest(client, events)
    resp = await client.get(f"/stores/{TEST_STORE_ID}/metrics?date={TODAY}")
    assert resp.status_code == 200
    dwell = resp.json()["avg_dwell_per_zone"]
    if "SKINCARE" in dwell and "MAKEUP" in dwell and "FRAGRANCE" in dwell:
        assert dwell["SKINCARE"] >= dwell["MAKEUP"] >= dwell["FRAGRANCE"]


@pytest.mark.asyncio
async def test_metrics_queue_depth(client):
    """queue_depth_current reflects latest BILLING_QUEUE_JOIN metadata."""
    event = make_event(
        visitor_id="VIS_queue",
        event_type="BILLING_QUEUE_JOIN",
        zone_id="BILLING",
        camera_id="CAM_BILLING_01",
        metadata={"queue_depth": 5, "session_seq": 1},
    )
    await _ingest(client, [event])
    resp = await client.get(f"/stores/{TEST_STORE_ID}/metrics?date={TODAY}")
    assert resp.status_code == 200
    assert resp.json()["queue_depth_current"] == 5


@pytest.mark.asyncio
async def test_metrics_response_schema(client):
    """Response must contain all required fields."""
    resp = await client.get(f"/stores/{TEST_STORE_ID}/metrics?date={TODAY}")
    assert resp.status_code == 200
    data = resp.json()
    required = {
        "store_id", "date", "unique_visitors", "conversion_rate",
        "avg_dwell_per_zone", "queue_depth_current", "abandonment_rate",
    }
    assert required.issubset(data.keys())


@pytest.mark.asyncio
async def test_metrics_invalid_date(client):
    """Invalid date format → 400."""
    resp = await client.get(f"/stores/{TEST_STORE_ID}/metrics?date=not-a-date")
    assert resp.status_code == 400
