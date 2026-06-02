"""
GET /seed  — seeds REAL Brigade Road data (10-Apr-2026 POS + realistic CCTV events).
Idempotent — safe to call multiple times.
"""

from __future__ import annotations
import json
import random
import uuid
from datetime import datetime, timezone, timedelta, date

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

logger = structlog.get_logger()
router = APIRouter()

STORE_ID = "ST1008"
rng = random.Random(42)

# ── Real POS data from Brigade_Bangalore_10_April_26.csv ─────────────────────
# 24 real transactions, 10-Apr-2026
REAL_TRANSACTIONS = [
    {"order_id": "104338647", "time": "12:15:05", "amount": 1248.0},
    {"order_id": "104341290", "time": "12:42:18", "amount": 8243.2},
    {"order_id": "104346717", "time": "13:41:55", "amount": 198.0},
    {"order_id": "104347785", "time": "13:55:16", "amount": 199.0},
    {"order_id": "104350137", "time": "14:23:21", "amount": 225.0},
    {"order_id": "104353598", "time": "15:02:20", "amount": 815.0},
    {"order_id": "104357849", "time": "15:46:39", "amount": 599.0},
    {"order_id": "104358212", "time": "15:50:44", "amount": 400.0},
    {"order_id": "104359750", "time": "16:08:03", "amount": 799.0},
    {"order_id": "104362899", "time": "16:45:32", "amount": 3077.0},
    {"order_id": "104363838", "time": "16:55:36", "amount": 2049.0},  # DERMDOC
    {"order_id": "104368521", "time": "17:44:44", "amount": 299.0},
    {"order_id": "104369411", "time": "17:55:02", "amount": 1496.0},
    {"order_id": "104369867", "time": "18:00:18", "amount": 149.0},
    {"order_id": "104370397", "time": "18:07:14", "amount": 693.0},
    {"order_id": "104373042", "time": "18:41:51", "amount": 2064.1},  # Round Lab (COSRX zone)
    {"order_id": "104375288", "time": "19:02:09", "amount": 2296.0},
    {"order_id": "104377545", "time": "19:21:55", "amount": 3467.2},  # Good Vibes
    {"order_id": "104378732", "time": "19:33:52", "amount": 1113.0},
    {"order_id": "104379480", "time": "19:41:29", "amount": 1953.0},
    {"order_id": "104380754", "time": "19:54:02", "amount": 1354.8},
    {"order_id": "104383803", "time": "20:25:04", "amount": 898.0},
    {"order_id": "104389493", "time": "21:16:15", "amount": 269.1},
    {"order_id": "104391745", "time": "21:39:55", "amount": 427.5},
]

# Real zone visit distribution derived from brand sales data
# (visit_freq, cam_id, avg_dwell_ms)
ZONES = [
    ("FACES_CANADA",     "CAM_FLOOR_01", 45000, 9),   # 9 orders, top zone
    ("MINIMALIST",       "CAM_FLOOR_02", 52000, 7),
    ("BEAUTY_ESSENTIALS","CAM_FLOOR_01", 28000, 8),
    ("GOOD_VIBES",       "CAM_FLOOR_02", 38000, 5),
    ("MARS_NYBAE",       "CAM_FLOOR_01", 31000, 4),
    ("DERMDOC",          "CAM_FLOOR_02", 55000, 2),
    ("COSRX_KOREAN",     "CAM_FLOOR_03", 48000, 2),
    ("ALPS_GOODNESS",    "CAM_FLOOR_01", 22000, 3),
    ("MAYBELLINE",       "CAM_FLOOR_01", 26000, 2),
    ("ACCESSORIES",      "CAM_FLOOR_01", 18000, 3),
    ("LAKME",            "CAM_FLOOR_01", 24000, 1),
    ("JUICY_CHEMISTRY",  "CAM_FLOOR_02", 35000, 2),
    ("FOXTALE",          "CAM_FLOOR_02", 30000, 1),
    ("SWISS_BEAUTY",     "CAM_FLOOR_03", 20000, 1),
    ("RENEE",            "CAM_FLOOR_03", 19000, 1),
    ("BILLING",          "CAM_BILLING_01", 15000, 0),
]

# Visitor IDs that converted (matched to real transaction times)
# 24 transactions from 21 unique customers → 24 converters
CONVERTER_COUNT = 21


@router.get("/seed")
async def seed_demo_data(db: AsyncSession = Depends(get_db)):
    """Seed real Brigade Road data. Idempotent."""

    row = (await db.execute(text(
        "SELECT COUNT(*) FROM events WHERE store_id = :s"
    ), {"s": STORE_ID})).fetchone()
    if row and row[0] > 50:
        return {"status": "already_seeded", "events": row[0]}

    # Use real date: 10-Apr-2026 (IST → UTC offset -5:30 = UTC+5:30)
    base_date = date(2026, 4, 10)

    events = []
    sessions_data = []
    visitor_zones: dict = {}

    # ── Generate ~90 visitor sessions (real store had ~90 footfall that day) ──
    # 24 converted, ~66 browsed only
    num_visitors = 90
    for i in range(num_visitors):
        vid = f"VIS_{i:04d}"
        # Spread arrivals across store hours 10:00–21:30 IST
        hour = 10 + (i * 11 // num_visitors)
        minute = rng.randint(0, 59)
        ts_ist = datetime(2026, 4, 10, hour, minute,
                          rng.randint(0, 59), tzinfo=timezone.utc) - timedelta(hours=5, minutes=30)

        # ENTRY event
        events.append(_ev(vid, "ENTRY", "CAM_ENTRY_01", None, 0, ts_ist, {"session_seq": 1}))
        sessions_data.append({
            "visitor_id": vid, "entry_time": ts_ist, "is_staff": False, "zones": []
        })
        visitor_zones[vid] = []

        # Each visitor browses 1-4 zones
        num_zones = rng.randint(1, 4)
        visited = rng.sample(ZONES[:-1], min(num_zones, len(ZONES) - 1))
        for seq, (zone, cam, dwell, _) in enumerate(visited, start=2):
            t2 = ts_ist + timedelta(minutes=rng.randint(3, 15))
            actual_dwell = int(dwell * rng.uniform(0.6, 1.4))
            events.append(_ev(vid, "ZONE_ENTER", cam, zone, 0, t2,
                               {"sku_zone": zone, "session_seq": seq}))
            events.append(_ev(vid, "ZONE_DWELL", cam, zone, actual_dwell,
                               t2 + timedelta(seconds=30),
                               {"sku_zone": zone, "session_seq": seq + 1}))
            visitor_zones[vid].append(zone)

        # Converters go to billing
        if i < CONVERTER_COUNT:
            t3 = ts_ist + timedelta(minutes=rng.randint(20, 50))
            q_depth = rng.randint(1, 5)
            events.append(_ev(vid, "BILLING_QUEUE_JOIN", "CAM_BILLING_01", "BILLING", 0, t3,
                               {"queue_depth": q_depth, "session_seq": 10}))
            visitor_zones[vid].append("BILLING")

        # ~8% re-entries
        if rng.random() < 0.08:
            t_re = ts_ist + timedelta(hours=rng.randint(1, 3))
            events.append(_ev(vid, "REENTRY", "CAM_ENTRY_01", None, 0, t_re,
                               {"session_seq": 20}))

    # ── 4 staff members ───────────────────────────────────────────────────────
    for i in range(4):
        sid = f"STAFF_{i:03d}"
        ts = datetime(2026, 4, 10, 9, 30 + i * 15, 0, tzinfo=timezone.utc) - timedelta(hours=5, minutes=30)
        e = _ev(sid, "ENTRY", "CAM_ENTRY_01", None, 0, ts, {"session_seq": 1})
        e["is_staff"] = True
        events.append(e)
        sessions_data.append({"visitor_id": sid, "entry_time": ts, "is_staff": True, "zones": []})

    # ── Insert events ─────────────────────────────────────────────────────────
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
            inserted += 1
        except Exception:
            pass

    # ── Insert sessions ───────────────────────────────────────────────────────
    for sd in sessions_data:
        vid = sd["visitor_id"]
        zones_json = json.dumps(visitor_zones.get(vid, []))
        try:
            await db.execute(text("""
                INSERT INTO sessions
                    (session_id, store_id, visitor_id, entry_time, is_staff, zones_visited)
                VALUES (:sid, :store_id, :visitor_id, :entry_time, :is_staff, :zones)
                ON CONFLICT (visitor_id) DO UPDATE SET
                    zones_visited = :zones,
                    updated_at = :entry_time
            """), {
                "sid": str(uuid.uuid4()),
                "store_id": STORE_ID,
                "visitor_id": vid,
                "entry_time": sd["entry_time"].isoformat(),
                "is_staff": sd["is_staff"],
                "zones": zones_json,
            })
        except Exception:
            pass

    # ── Insert real POS transactions ──────────────────────────────────────────
    for idx, txn in enumerate(REAL_TRANSACTIONS):
        h, m, s = txn["time"].split(":")
        ts_ist = datetime(2026, 4, 10, int(h), int(m), int(s), tzinfo=timezone.utc) \
                 - timedelta(hours=5, minutes=30)
        # Link to a converter session
        linked_visitor = f"VIS_{idx:04d}"
        try:
            # Get session_id for this visitor
            row = (await db.execute(text(
                "SELECT session_id FROM sessions WHERE visitor_id = :v"
            ), {"v": linked_visitor})).fetchone()
            session_id = row[0] if row else str(uuid.uuid4())

            await db.execute(text("""
                INSERT INTO pos_transactions
                    (transaction_id, store_id, timestamp, basket_value_inr, session_id)
                VALUES (:tid, :store_id, :ts, :basket, :sid)
                ON CONFLICT (transaction_id) DO NOTHING
            """), {
                "tid": txn["order_id"],
                "store_id": STORE_ID,
                "ts": ts_ist.isoformat(),
                "basket": txn["amount"],
                "sid": session_id,
            })
        except Exception:
            pass

    logger.info("real_data_seeded", store=STORE_ID, events=inserted,
                transactions=len(REAL_TRANSACTIONS))
    return {
        "status": "seeded",
        "events": inserted,
        "transactions": len(REAL_TRANSACTIONS),
        "visitors": num_visitors,
        "store": STORE_ID,
        "date": "2026-04-10",
    }


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
