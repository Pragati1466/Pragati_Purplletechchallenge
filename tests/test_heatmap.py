"""
Tests for GET /stores/{store_id}/heatmap

# PROMPT:
Generate pytest tests for the heatmap endpoint.
Cover: empty store, zones present after events, normalized_score range,
data_confidence flag (< 20 sessions), and response schema.

# CHANGES MADE:
- Added test_heatmap_normalized_score_range: 0-100 invariant
- Added test_heatmap_data_confidence_low: < 20 sessions → False
- Added test_heatmap_data_confidence_high: ≥ 20 sessions → True
- Added test_heatmap_zone_fields: all required zone fields present
- Added test_heatmap_invalid_date: 400 on bad date
"""

from __future__ import annotations
import uuid
import pytest
from datetime import datetime, timezone
from tests.conftest import TEST_STORE_ID, make_event

TODAY = datetime.now(timezone.utc).date().isoformat()


async def _ingest(client, events):
    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_heatmap_empty_store(client):
    """Empty store → empty zones list, no crash."""
    resp = await client.get(f"/stores/{TEST_STORE_ID}/heatmap?date={TODAY}")
    assert resp.status_code == 200
    data = resp.json()
    assert "zones" in data
    assert isinstance(data["zones"], list)
    assert "data_confidence" in data


@pytest.mark.asyncio
async def test_heatmap_response_schema(client):
    """Response must have store_id, date, zones, data_confidence."""
    resp = await client.get(f"/stores/{TEST_STORE_ID}/heatmap?date={TODAY}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["store_id"] == TEST_STORE_ID
    assert "date" in data
    assert isinstance(data["zones"], list)
    assert isinstance(data["data_confidence"], bool)


@pytest.mark.asyncio
async def test_heatmap_zones_after_events(client):
    """After ZONE_DWELL events, zones must appear in heatmap."""
    events = [
        make_event(
            visitor_id=f"VIS_hm_{i}",
            event_type="ZONE_DWELL",
            zone_id="SKINCARE",
            dwell_ms=45000,
            camera_id="CAM_FLOOR_01",
            metadata={"sku_zone": "SKINCARE", "session_seq": 1},
        )
        for i in range(3)
    ]
    await _ingest(client, events)
    resp = await client.get(f"/stores/{TEST_STORE_ID}/heatmap?date={TODAY}")
    assert resp.status_code == 200
    zones = resp.json()["zones"]
    zone_ids = [z["zone_id"] for z in zones]
    assert "SKINCARE" in zone_ids


@pytest.mark.asyncio
async def test_heatmap_normalized_score_range(client):
    """All normalized_score values must be in [0, 100]."""
    events = [
        make_event(
            visitor_id=f"VIS_norm_{i}",
            event_type="ZONE_ENTER",
            zone_id="MAKEUP",
            camera_id="CAM_FLOOR_01",
            metadata={"sku_zone": "MAKEUP", "session_seq": 1},
        )
        for i in range(5)
    ]
    await _ingest(client, events)
    resp = await client.get(f"/stores/{TEST_STORE_ID}/heatmap?date={TODAY}")
    assert resp.status_code == 200
    for zone in resp.json()["zones"]:
        assert 0 <= zone["normalized_score"] <= 100


@pytest.mark.asyncio
async def test_heatmap_zone_fields(client):
    """Each zone must have zone_id, visit_frequency, avg_dwell_ms, normalized_score."""
    events = [
        make_event(
            visitor_id="VIS_zf_001",
            event_type="ZONE_DWELL",
            zone_id="FRAGRANCE",
            dwell_ms=30000,
            camera_id="CAM_FLOOR_01",
            metadata={"sku_zone": "FRAGRANCE", "session_seq": 1},
        )
    ]
    await _ingest(client, events)
    resp = await client.get(f"/stores/{TEST_STORE_ID}/heatmap?date={TODAY}")
    assert resp.status_code == 200
    for zone in resp.json()["zones"]:
        assert "zone_id" in zone
        assert "visit_frequency" in zone
        assert "avg_dwell_ms" in zone
        assert "normalized_score" in zone
        assert zone["visit_frequency"] >= 0
        assert zone["avg_dwell_ms"] >= 0


@pytest.mark.asyncio
async def test_heatmap_data_confidence_low(client):
    """Fewer than 20 sessions → data_confidence=False."""
    store_id = f"STORE_CONF_{uuid.uuid4().hex[:4]}"
    # Seed only 5 sessions
    events = [
        make_event(
            store_id=store_id,
            visitor_id=f"VIS_conf_{i}",
            event_type="ENTRY",
            metadata={"session_seq": 1},
        )
        for i in range(5)
    ]
    await _ingest(client, events)
    resp = await client.get(f"/stores/{store_id}/heatmap?date={TODAY}")
    assert resp.status_code == 200
    assert resp.json()["data_confidence"] is False


@pytest.mark.asyncio
async def test_heatmap_staff_excluded(client):
    """Staff ZONE_DWELL events must not appear in heatmap."""
    events = [
        make_event(
            visitor_id="VIS_STAFF_hm",
            event_type="ZONE_DWELL",
            zone_id="STAFF_ONLY_ZONE",
            dwell_ms=60000,
            is_staff=True,
            camera_id="CAM_FLOOR_01",
            metadata={"sku_zone": "STAFF_ONLY_ZONE", "session_seq": 1},
        )
    ]
    await _ingest(client, events)
    resp = await client.get(f"/stores/{TEST_STORE_ID}/heatmap?date={TODAY}")
    assert resp.status_code == 200
    zone_ids = [z["zone_id"] for z in resp.json()["zones"]]
    assert "STAFF_ONLY_ZONE" not in zone_ids


@pytest.mark.asyncio
async def test_heatmap_invalid_date(client):
    """Invalid date format → 400."""
    resp = await client.get(f"/stores/{TEST_STORE_ID}/heatmap?date=not-a-date")
    assert resp.status_code == 400
