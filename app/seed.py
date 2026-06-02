"""
GET /seed  — seeds realistic demo data into the DB on first call.
Called automatically by the dashboard on page load.
Idempotent — safe to call multiple times.
"""

from __future__ import annotations
import json
import random
import uuid
from datetime import datetime, timezone, timedelta

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

logger = structlog.get_logger()
router = APIRouter()

STORE_ID = "ST1008"
rng = random.Random(42)

ZONES = [
    ("FACES_CANADA",  "CAM_FLOOR_01", 45000),
    ("GOOD_VIBES",    "CAM_FLOOR_02", 38000),
    ("DERMDOC",       "CAM_FLOOR_02", 52000),
    ("MINIMALIST",    "CAM_FLOOR_02", 31000),
    ("MAYBELLINE",    "CAM_FLOOR_01", 28000),
    ("ALPS_GOODNESS", "CAM_FLOOR_01", 22000),
    ("COSRX_KOREAN",  "CAM_FLOOR_03", 41000),
    ("BILLING",       "CAM_BILLING_01", 15000),
]


@router.get("/seed")
async def seed_demo_data(db: AsyncSession = Depends(get_db)):
    """Seed demo data. Idempotent."""

    # Check if already seeded
    row = (await db.execute(text(
        "SELECT COUNT(*) FROM events WHERE store_id = :s"
    ), {"s": STORE_ID})).fetchone()
    if row and row[0] > 50:
        return {"status": "already_seeded", "events": row[0]}

    now = datetime.now(timezone.utc)
    events = []

    # 35 visitor sessions
    for i in range(35):
        vid = f"VIS_demo_{i:03d}"
        t = now - timedelta(minutes=i * 4 + rng.randint(0, 3))

        # ENTRY
        events.append(_ev(vid, "ENTRY", "CAM_ENTRY_01", None, 0, t, {"session_seq": 1}))

        # Zone visit
        zone, cam, dwell = ZONES[i % len(ZONES)]
        t2 = t + timedelta(minutes=2)
        events.append(_ev(vid, "ZONE_ENTER", cam, zone, 0, t2, {"sku_zone": zone, "session_seq": 2}))
        events.append(_ev(vid, "ZONE_DWELL", cam, zone, dwell, t2 + timedelta(seconds=30), {"sku_zone": zone, "session_seq": 3}))

        # Billing for some
        if i % 4 == 0:
            t3 = t + timedelta(minutes=6)
            events.append(_ev(vid, "BILLING_QUEUE_JOIN", "CAM_BILLING_01", "BILLING", 0, t3,
                               {"queue_depth": rng.randint(1, 4), "session_seq": 4}))

    # 3 staff
    for i in range(3):
        sid = f"STAFF_{i:03d}"
        t = now - timedelta(hours=2, minutes=i * 30)
        e = _ev(sid, "ENTRY", "CAM_ENTRY_01", None, 0, t, {"session_seq": 1})
        e["is_staff"] = True
        events.append(e)

    # Insert events
    inserted = 0
    for ev in events:
        try:
            await db.execute(text("""
                INSERT INTO events
                    (event_id, store_id, camera_id, visitor_id, event_type,
                     timestamp, zone_id, dwell_ms, is_staff, confidence, metadata)
                VALUES
                    (:event_id, :store_id, :camera_id, :visitor_id, :event_type,
                     :timestamp, :zone_id, :dwell_ms, :is_staff, :confidence, :metadata)
                ON CONFLICT (event_id) DO NOTHING
            """), ev)

            await db.execute(text("""
                INSERT INTO sessions
                    (session_id, store_id, visitor_id, entry_time, is_staff)
                VALUES (:sid, :store_id, :visitor_id, :entry_time, :is_staff)
                ON CONFLICT (visitor_id) DO UPDATE SET
                    updated_at = :entry_time
            """), {
                "sid": str(uuid.uuid4()),
                "store_id": ev["store_id"],
                "visitor_id": ev["visitor_id"],
                "entry_time": ev["timestamp"],
                "is_staff": ev["is_staff"],
            })

            if ev["event_type"] == "ZONE_ENTER" and ev["zone_id"]:
                row = (await db.execute(text(
                    "SELECT zones_visited FROM sessions WHERE visitor_id = :v"
                ), {"v": ev["visitor_id"]})).fetchone()
                if row:
                    try:
                        zl = json.loads(row[0]) if row[0] else []
                    except Exception:
                        zl = []
                    if ev["zone_id"] not in zl:
                        zl.append(ev["zone_id"])
                    await db.execute(text(
                        "UPDATE sessions SET zones_visited = :z WHERE visitor_id = :v"
                    ), {"z": json.dumps(zl), "v": ev["visitor_id"]})

            inserted += 1
        except Exception:
            pass

    logger.info("demo_seeded", events=inserted)
    return {"status": "seeded", "events": inserted}


def _ev(visitor_id, event_type, camera_id, zone_id, dwell_ms, ts, metadata):
    return {
        "event_id":   str(uuid.uuid4()),
        "store_id":   STORE_ID,
        "camera_id":  camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp":  ts.isoformat().replace("+00:00", "Z"),
        "zone_id":    zone_id,
        "dwell_ms":   dwell_ms,
        "is_staff":   False,
        "confidence": round(rng.uniform(0.80, 0.97), 2),
        "metadata":   json.dumps(metadata),
    }
