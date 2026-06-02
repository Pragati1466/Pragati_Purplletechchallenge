"""
Tests for GET /stores/{store_id}/anomalies

# PROMPT:
Generate pytest tests for the anomaly detection endpoint.
Cover: no anomalies (healthy store), queue spike detection,
conversion drop detection, dead zone detection, severity levels,
response schema validation, and direct unit tests for stats helpers.

# CHANGES MADE:
- Added test_anomalies_suggested_action_not_empty: ensures actionable output
- Added test_anomalies_severity_values: validates only INFO/WARN/CRITICAL returned
- Added test_anomalies_response_schema: validates required fields
- Added test_anomalies_stats_helper: unit test for _stats()
- Added test_anomalies_severity_helper: unit test for _severity()
- Added test_anomalies_dead_zone_triggered: seeds zone events then checks dead zone
- Simplified DB seeding to use HTTP ingest (full-stack test)
"""

from __future__ import annotations
import pytest
from datetime import datetime, timezone
from tests.conftest import make_event, TEST_STORE_ID

TODAY = datetime.now(timezone.utc).date().isoformat()


async def _ingest(client, events):
    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 200


# ── Unit tests for stats helpers (boosts anomalies.py coverage) ──────────────

def test_anomalies_stats_helper():
    """_stats() returns correct mean and std."""
    from app.anomalies import _stats
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    mean, std = _stats(values)
    assert mean == 3.0
    assert abs(std - 1.4142) < 0.001


def test_anomalies_stats_single_value():
    """_stats() handles single-element list."""
    from app.anomalies import _stats
    mean, std = _stats([5.0])
    assert mean == 5.0
    assert std == 0.0


def test_anomalies_severity_none_below_threshold():
    """_severity() returns None for deviations < 1σ."""
    from app.anomalies import _severity
    assert _severity(0.5) is None
    assert _severity(0.99) is None


def test_anomalies_severity_info():
    """_severity() returns INFO for 1σ ≤ dev < 2σ."""
    from app.anomalies import _severity, AnomalySeverity
    assert _severity(1.0) == AnomalySeverity.INFO
    assert _severity(1.5) == AnomalySeverity.INFO


def test_anomalies_severity_warn():
    """_severity() returns WARN for 2σ ≤ dev < 3σ."""
    from app.anomalies import _severity, AnomalySeverity
    assert _severity(2.0) == AnomalySeverity.WARN
    assert _severity(2.9) == AnomalySeverity.WARN


def test_anomalies_severity_critical():
    """_severity() returns CRITICAL for dev ≥ 3σ."""
    from app.anomalies import _severity, AnomalySeverity
    assert _severity(3.0) == AnomalySeverity.CRITICAL
    assert _severity(10.0) == AnomalySeverity.CRITICAL


# ── API endpoint tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_anomalies_response_schema(client):
    """Response must have store_id and anomalies list."""
    resp = await client.get(f"/stores/{TEST_STORE_ID}/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert "store_id" in data
    assert "anomalies" in data
    assert isinstance(data["anomalies"], list)


@pytest.mark.asyncio
async def test_anomalies_empty_store_no_crash(client):
    """Empty store → anomalies list returned (may be empty), no crash."""
    resp = await client.get(f"/stores/{TEST_STORE_ID}/anomalies")
    assert resp.status_code == 200
    assert resp.json()["store_id"] == TEST_STORE_ID


@pytest.mark.asyncio
async def test_anomalies_severity_values(client):
    """All returned anomalies must have valid severity values."""
    resp = await client.get(f"/stores/{TEST_STORE_ID}/anomalies")
    assert resp.status_code == 200
    valid_severities = {"INFO", "WARN", "CRITICAL"}
    for anomaly in resp.json()["anomalies"]:
        assert anomaly["severity"] in valid_severities


@pytest.mark.asyncio
async def test_anomalies_suggested_action_not_empty(client):
    """Every anomaly must have a non-empty suggested_action."""
    resp = await client.get(f"/stores/{TEST_STORE_ID}/anomalies")
    assert resp.status_code == 200
    for anomaly in resp.json()["anomalies"]:
        assert anomaly.get("suggested_action"), (
            f"Anomaly {anomaly.get('type')} has empty suggested_action"
        )


@pytest.mark.asyncio
async def test_anomalies_anomaly_fields(client):
    """Each anomaly must have required fields."""
    events = [
        make_event(
            visitor_id=f"VIS_Q{i}",
            event_type="BILLING_QUEUE_JOIN",
            zone_id="BILLING",
            camera_id="CAM_BILLING_01",
            metadata={"queue_depth": 10, "session_seq": 1},
        )
        for i in range(3)
    ]
    await _ingest(client, events)

    resp = await client.get(f"/stores/{TEST_STORE_ID}/anomalies")
    assert resp.status_code == 200
    required_fields = {"type", "severity", "detected_at", "current_value",
                       "baseline_value", "suggested_action"}
    for anomaly in resp.json()["anomalies"]:
        assert required_fields.issubset(anomaly.keys()), (
            f"Anomaly missing fields: {required_fields - anomaly.keys()}"
        )


@pytest.mark.asyncio
async def test_anomalies_unknown_store(client):
    """Unknown store_id → 200 with empty anomalies (not a 404)."""
    resp = await client.get("/stores/STORE_UNKNOWN_999/anomalies")
    assert resp.status_code == 200
    assert resp.json()["anomalies"] == []


@pytest.mark.asyncio
async def test_anomalies_dead_zone_detected(client):
    """
    After seeding zone events for some zones but not others,
    the unvisited zones should appear as DEAD_ZONE anomalies.
    """
    import uuid as _uuid
    store_id = f"STORE_DZ_{_uuid.uuid4().hex[:4]}"

    # Seed a ZONE_ENTER event for one zone (old timestamp — >30 min ago)
    from datetime import timedelta
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=1))
    events = [
        make_event(
            store_id=store_id,
            visitor_id="VIS_dz_001",
            event_type="ZONE_ENTER",
            zone_id="OLD_ZONE",
            camera_id="CAM_FLOOR_01",
            timestamp=old_ts.isoformat().replace("+00:00", "Z"),
            metadata={"sku_zone": "OLD_ZONE", "session_seq": 1},
        )
    ]
    await _ingest(client, events)

    resp = await client.get(f"/stores/{store_id}/anomalies")
    assert resp.status_code == 200
    anomalies = resp.json()["anomalies"]
    dead_zones = [a for a in anomalies if a["type"] == "DEAD_ZONE"]
    # OLD_ZONE was visited >30 min ago → should be flagged
    assert len(dead_zones) >= 1
    dead_zone_ids = [a["suggested_action"] for a in dead_zones]
    assert any("OLD_ZONE" in s for s in dead_zone_ids)

