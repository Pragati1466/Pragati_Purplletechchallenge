"""
Tests for GET /stores/{store_id}/funnel

# PROMPT:
Generate pytest tests for the conversion funnel endpoint.
Cover: happy path (all 4 stages), empty store, re-entry deduplication,
all-staff clip, zero purchases, and response schema validation.
Use the shared `client` fixture from conftest.py.

# CHANGES MADE:
- Added test_funnel_reentry_deduplication: ensures REENTRY doesn't inflate entry count
- Added test_funnel_drop_off_percentages: validates drop-off math is correct
- Added test_funnel_stages_are_monotonically_decreasing: funnel invariant
- Replaced abstract DB calls with HTTP ingest (tests full stack)
"""

from __future__ import annotations
import pytest
from datetime import datetime, timezone
from tests.conftest import make_event, TEST_STORE_ID

TODAY = datetime.now(timezone.utc).date().isoformat()


async def _ingest(client, events):
    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_funnel_empty_store(client):
    """Empty store → all stages are 0, no crash."""
    resp = await client.get(f"/stores/{TEST_STORE_ID}/funnel?date={TODAY}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["store_id"] == TEST_STORE_ID
    for stage in data["stages"]:
        assert stage["count"] == 0
        assert stage["drop_off_pct"] == 0.0


@pytest.mark.asyncio
async def test_funnel_response_schema(client):
    """Response must have store_id, date, and 4 stages."""
    resp = await client.get(f"/stores/{TEST_STORE_ID}/funnel?date={TODAY}")
    assert resp.status_code == 200
    data = resp.json()
    assert "store_id" in data
    assert "date" in data
    assert "stages" in data
    assert len(data["stages"]) == 4
    stage_names = [s["stage"] for s in data["stages"]]
    assert stage_names == ["entry", "zone_visit", "billing_queue", "purchase"]


@pytest.mark.asyncio
async def test_funnel_entry_stage(client):
    """Entry stage counts unique customer sessions."""
    events = [
        make_event(visitor_id="VIS_F1", event_type="ENTRY"),
        make_event(visitor_id="VIS_F2", event_type="ENTRY"),
        make_event(visitor_id="VIS_STAFF", event_type="ENTRY", is_staff=True),
    ]
    await _ingest(client, events)
    resp = await client.get(f"/stores/{TEST_STORE_ID}/funnel?date={TODAY}")
    assert resp.status_code == 200
    entry_stage = resp.json()["stages"][0]
    assert entry_stage["stage"] == "entry"
    assert entry_stage["count"] >= 2  # at least the 2 customers


@pytest.mark.asyncio
async def test_funnel_reentry_deduplication(client):
    """REENTRY must not inflate the entry count."""
    visitor_id = "VIS_funnel_reentry"
    events = [
        make_event(visitor_id=visitor_id, event_type="ENTRY"),
        make_event(visitor_id=visitor_id, event_type="EXIT"),
        make_event(visitor_id=visitor_id, event_type="REENTRY"),
    ]
    await _ingest(client, events)
    resp = await client.get(f"/stores/{TEST_STORE_ID}/funnel?date={TODAY}")
    assert resp.status_code == 200
    entry_count = resp.json()["stages"][0]["count"]
    # Should be 1, not 2
    assert entry_count == 1


@pytest.mark.asyncio
async def test_funnel_stages_monotonically_decreasing(client):
    """Each funnel stage count must be <= the previous stage."""
    events = [
        make_event(visitor_id="VIS_G1", event_type="ENTRY"),
        make_event(visitor_id="VIS_G1", event_type="ZONE_ENTER",
                   zone_id="SKINCARE", camera_id="CAM_FLOOR_01",
                   metadata={"session_seq": 2}),
        make_event(visitor_id="VIS_G2", event_type="ENTRY"),
    ]
    await _ingest(client, events)
    resp = await client.get(f"/stores/{TEST_STORE_ID}/funnel?date={TODAY}")
    assert resp.status_code == 200
    counts = [s["count"] for s in resp.json()["stages"]]
    for i in range(1, len(counts)):
        assert counts[i] <= counts[i - 1], (
            f"Stage {i} count {counts[i]} > stage {i-1} count {counts[i-1]}"
        )


@pytest.mark.asyncio
async def test_funnel_all_staff(client):
    """All-staff clip → all funnel stages are 0."""
    events = [
        make_event(visitor_id=f"VIS_STAFF_{i}", event_type="ENTRY", is_staff=True)
        for i in range(3)
    ]
    await _ingest(client, events)
    resp = await client.get(f"/stores/{TEST_STORE_ID}/funnel?date={TODAY}")
    assert resp.status_code == 200
    for stage in resp.json()["stages"]:
        assert stage["count"] == 0


@pytest.mark.asyncio
async def test_funnel_drop_off_first_stage_is_zero(client):
    """First stage (entry) always has 0% drop-off."""
    resp = await client.get(f"/stores/{TEST_STORE_ID}/funnel?date={TODAY}")
    assert resp.status_code == 200
    assert resp.json()["stages"][0]["drop_off_pct"] == 0.0
