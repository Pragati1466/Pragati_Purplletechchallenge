"""
init_real_data.py
==================
Seeds the database with real Brigade Road data WITHOUT needing to run
the full CCTV detection pipeline first.

What it does:
  1. Registers store ST1008 in the stores table
  2. Seeds 24 real POS transactions from the CSV
  3. Generates realistic visitor events derived from the POS timeline:
     - Each transaction → 1 converted visitor session
     - Additional browse-only sessions (non-converting) based on typical
       beauty retail conversion rate (~18-22%)
     - Zone visits derived from the actual brands/departments purchased
  4. Correlates sessions with POS transactions

This gives the API real, queryable data immediately — before the
CCTV pipeline has been run.

Usage:
    python data/init_real_data.py
    python data/init_real_data.py --db-url postgresql+asyncpg://...
"""

import argparse
import asyncio
import csv
import json
import os
import random
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text

# ── Constants ─────────────────────────────────────────────────────────────────

STORE_ID   = "ST1008"
STORE_NAME = "Brigade_Bangalore"
CITY       = "Bangalore"
DATE_STR   = "2026-04-10"
POS_CSV    = Path(__file__).parent / "pos_transactions.csv"
LAYOUT     = Path(__file__).parent / "store_layout.json"

# Typical beauty retail: ~20% conversion rate
# So for every buyer, ~4 more visitors browse without buying
BROWSE_MULTIPLIER = 4

# Department → zone mapping (from store_layout.json)
DEPT_TO_ZONES = {
    "makeup":        ["MAYBELLINE", "LAKME", "FACES_CANADA", "MARS_NYBAE",
                      "LOREAL", "BEAUTY_ESSENTIALS", "SWISS_BEAUTY", "RENEE"],
    "skin":          ["AQUALOGICA", "TFS", "GOOD_VIBES", "DERMDOC",
                      "FOXTALE", "MINIMALIST", "PILGRIM", "COSRX_KOREAN"],
    "hair":          ["ALPS_GOODNESS"],
    "personal-care": ["ACCESSORIES", "MENS_CARE"],
    "bath-and-body": ["BEAUTY_ESSENTIALS"],
    "fragrance":     ["BEAUTY_ESSENTIALS"],
}

# Brand → zone (more specific)
BRAND_TO_ZONE = {
    "Maybelline":      "MAYBELLINE",
    "Lakme":           "LAKME",
    "Faces Canada":    "FACES_CANADA",
    "FACES CANADA":    "FACES_CANADA",
    "NY Bae":          "MARS_NYBAE",
    "Mars":            "MARS_NYBAE",
    "Alps Goodness":   "ALPS_GOODNESS",
    "Good Vibes":      "GOOD_VIBES",
    "DERMDOC":         "DERMDOC",
    "Foxtale":         "FOXTALE",
    "FoxTale":         "FOXTALE",
    "Minimalist":      "MINIMALIST",
    "Swiss Beauty":    "SWISS_BEAUTY",
    "Renee":           "RENEE",
    "RENEE":           "RENEE",
    "Juicy Chemistry": "JUICY_CHEMISTRY",
    "COSRX":           "COSRX_KOREAN",
    "Beauty of Joseon":"COSRX_KOREAN",
    "Round Lab":       "COSRX_KOREAN",
    "Bare Anatomy":    "FOXTALE",
    "Neutrogena":      "AQUALOGICA",
    "Garnier":         "LOREAL",
    "Lotus Herbals":   "AQUALOGICA",
    "Carmesi":         "ACCESSORIES",
    "GUBB":            "ACCESSORIES",
    "Purplle":         "BEAUTY_ESSENTIALS",
    "Cuffs N Lashes":  "ACCESSORIES",
}

rng = random.Random(42)   # deterministic for reproducibility


# ── Helpers ───────────────────────────────────────────────────────────────────

def ts(time_str: str, offset_sec: int = 0) -> str:
    """Build ISO-8601 UTC timestamp from HH:MM:SS string + offset."""
    base = datetime.fromisoformat(f"{DATE_STR}T{time_str}+00:00")
    return (base + timedelta(seconds=offset_sec)).isoformat().replace("+00:00", "Z")


def make_event(
    store_id, camera_id, visitor_id, event_type,
    timestamp, zone_id, dwell_ms, is_staff, confidence, metadata
) -> dict:
    return {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store_id,
        "camera_id":  camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp":  timestamp,
        "zone_id":    zone_id,
        "dwell_ms":   dwell_ms,
        "is_staff":   is_staff,
        "confidence": confidence,
        "metadata":   json.dumps(metadata),
    }


def zones_for_transaction(items: list) -> list:
    """Return ordered list of zones a buyer likely visited based on their purchases."""
    zones = []
    for item in items:
        brand = item.get("brand", "")
        dept  = item.get("department", "")
        zone  = BRAND_TO_ZONE.get(brand)
        if not zone:
            candidates = DEPT_TO_ZONES.get(dept, [])
            zone = rng.choice(candidates) if candidates else None
        if zone and zone not in zones:
            zones.append(zone)
    return zones


def generate_session_events(
    visitor_id: str,
    entry_time_str: str,
    zones: list,
    is_buyer: bool,
    session_seq_start: int = 1,
) -> list:
    """Generate a realistic sequence of events for one visitor session."""
    events = []
    seq = session_seq_start
    t_offset = 0

    # ENTRY
    events.append(make_event(
        STORE_ID, "CAM_ENTRY_01", visitor_id, "ENTRY",
        ts(entry_time_str, t_offset), None, 0, False,
        round(rng.uniform(0.82, 0.97), 2),
        {"session_seq": seq},
    ))
    seq += 1
    t_offset += rng.randint(5, 15)

    # Zone visits
    for zone in zones:
        cam = _zone_to_camera(zone)
        dwell = rng.randint(25000, 90000)  # 25–90 seconds

        # ZONE_ENTER
        events.append(make_event(
            STORE_ID, cam, visitor_id, "ZONE_ENTER",
            ts(entry_time_str, t_offset), zone, 0, False,
            round(rng.uniform(0.75, 0.95), 2),
            {"sku_zone": zone, "session_seq": seq},
        ))
        seq += 1
        t_offset += 10

        # ZONE_DWELL (if stayed 30+ seconds)
        if dwell >= 30000:
            events.append(make_event(
                STORE_ID, cam, visitor_id, "ZONE_DWELL",
                ts(entry_time_str, t_offset + 30), zone, dwell, False,
                round(rng.uniform(0.70, 0.92), 2),
                {"sku_zone": zone, "session_seq": seq},
            ))
            seq += 1

        # ZONE_EXIT
        t_offset += dwell // 1000
        events.append(make_event(
            STORE_ID, cam, visitor_id, "ZONE_EXIT",
            ts(entry_time_str, t_offset), zone, dwell, False,
            round(rng.uniform(0.75, 0.95), 2),
            {"sku_zone": zone, "session_seq": seq},
        ))
        seq += 1
        t_offset += rng.randint(5, 20)

    # Billing queue (buyers only)
    if is_buyer:
        queue_depth = rng.randint(0, 3)
        if queue_depth > 0:
            events.append(make_event(
                STORE_ID, "CAM_BILLING_01", visitor_id, "BILLING_QUEUE_JOIN",
                ts(entry_time_str, t_offset), "BILLING", 0, False,
                round(rng.uniform(0.80, 0.96), 2),
                {"queue_depth": queue_depth, "session_seq": seq},
            ))
            seq += 1
        t_offset += rng.randint(120, 300)  # 2–5 min at billing

    # EXIT
    events.append(make_event(
        STORE_ID, "CAM_ENTRY_01", visitor_id, "EXIT",
        ts(entry_time_str, t_offset), None, 0, False,
        round(rng.uniform(0.80, 0.96), 2),
        {"session_seq": seq},
    ))

    return events


def _zone_to_camera(zone: str) -> str:
    floor1 = {"MAYBELLINE","LAKME","FACES_CANADA","MARS_NYBAE",
               "ALPS_GOODNESS","LOREAL","BEAUTY_ESSENTIALS","ACCESSORIES"}
    floor2 = {"JUICY_CHEMISTRY","AQUALOGICA","TFS","GOOD_VIBES",
               "DERMDOC","FOXTALE","MINIMALIST","MENS_CARE"}
    floor3 = {"SWISS_BEAUTY","RENEE","PILGRIM","SALM_EB","COSRX_KOREAN"}
    if zone in floor1:   return "CAM_FLOOR_01"
    if zone in floor2:   return "CAM_FLOOR_02"
    if zone in floor3:   return "CAM_FLOOR_03"
    if zone == "BILLING": return "CAM_BILLING_01"
    return "CAM_FLOOR_01"


# ── Database operations ───────────────────────────────────────────────────────

async def seed(db_url: str) -> None:
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Load POS CSV
    pos_rows = []
    with open(POS_CSV, newline="") as f:
        for row in csv.DictReader(f):
            pos_rows.append(row)

    # Load detailed POS JSONL for brand info
    pos_jsonl = Path(__file__).parent / "pos_transactions.jsonl"
    pos_detail = {}
    if pos_jsonl.exists():
        with open(pos_jsonl) as f:
            for line in f:
                d = json.loads(line)
                pos_detail[d["transaction_id"]] = d

    async with factory() as session:
        # 1. Register store
        print("Registering store ST1008...")
        await session.execute(text("""
            INSERT INTO stores (store_id, store_name, city, open_hours, zones, cameras)
            VALUES (:sid, :name, :city, :hours, :zones, :cameras)
            ON CONFLICT (store_id) DO UPDATE SET
                store_name = EXCLUDED.store_name,
                city = EXCLUDED.city
        """), {
            "sid":     STORE_ID,
            "name":    STORE_NAME,
            "city":    CITY,
            "hours":   json.dumps({"open": "10:00", "close": "22:00"}),
            "zones":   json.dumps(list(BRAND_TO_ZONE.values())),
            "cameras": json.dumps(["CAM_ENTRY_01","CAM_FLOOR_01","CAM_FLOOR_02",
                                    "CAM_BILLING_01","CAM_FLOOR_03"]),
        })

        # 2. Seed POS transactions
        print(f"Seeding {len(pos_rows)} POS transactions...")
        for row in pos_rows:
            await session.execute(text("""
                INSERT INTO pos_transactions
                    (transaction_id, store_id, timestamp, basket_value_inr)
                VALUES (:txn_id, :store_id, :ts, :basket)
                ON CONFLICT (transaction_id) DO NOTHING
            """), {
                "txn_id":   row["transaction_id"],
                "store_id": row["store_id"],
                "ts":       row["timestamp"],
                "basket":   float(row["basket_value_inr"]),
            })

        # 3. Generate visitor sessions from POS data
        print("Generating visitor sessions from POS timeline...")
        all_events = []
        session_to_txn = {}   # visitor_id → transaction_id

        for row in pos_rows:
            txn_id   = row["transaction_id"]
            txn_time = row["timestamp"][11:19]   # HH:MM:SS
            detail   = pos_detail.get(txn_id, {})
            items    = detail.get("items", [])

            # Buyer entered ~5–15 min before transaction
            entry_offset = -rng.randint(300, 900)
            entry_time_dt = datetime.fromisoformat(
                f"{DATE_STR}T{txn_time}+00:00"
            ) + timedelta(seconds=entry_offset)
            entry_time_str = entry_time_dt.strftime("%H:%M:%S")

            visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
            zones = zones_for_transaction(items)
            if not zones:
                zones = [rng.choice(list(BRAND_TO_ZONE.values()))]

            events = generate_session_events(
                visitor_id, entry_time_str, zones, is_buyer=True
            )
            all_events.extend(events)
            session_to_txn[visitor_id] = txn_id

        # 4. Generate browse-only sessions (non-converting)
        # Spread across the operating hours (12:00–21:40)
        n_browsers = len(pos_rows) * BROWSE_MULTIPLIER
        print(f"Generating {n_browsers} browse-only sessions...")
        for _ in range(n_browsers):
            # Random entry time between 12:00 and 21:00
            entry_hour  = rng.randint(12, 20)
            entry_min   = rng.randint(0, 59)
            entry_sec   = rng.randint(0, 59)
            entry_str   = f"{entry_hour:02d}:{entry_min:02d}:{entry_sec:02d}"

            visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
            # Browse 1–3 zones
            dept = rng.choice(list(DEPT_TO_ZONES.keys()))
            zones = rng.sample(DEPT_TO_ZONES[dept], min(rng.randint(1, 3), len(DEPT_TO_ZONES[dept])))

            events = generate_session_events(
                visitor_id, entry_str, zones, is_buyer=False
            )
            all_events.extend(events)

        # 5. Add 3 staff sessions (is_staff=True)
        print("Adding staff movement events...")
        for i in range(3):
            staff_id = f"STAFF_{i+1:03d}"
            staff_entry = f"{rng.randint(9,10):02d}:{rng.randint(0,59):02d}:00"
            # Staff visits all zones
            staff_zones = rng.sample(list(BRAND_TO_ZONE.values()), 5)
            events = generate_session_events(staff_id, staff_entry, staff_zones, is_buyer=False)
            # Mark all as staff
            for e in events:
                e["is_staff"] = True
            all_events.extend(events)

        # 6. Insert all events
        print(f"Inserting {len(all_events)} events...")
        for ev in all_events:
            await session.execute(text("""
                INSERT INTO events (
                    event_id, store_id, camera_id, visitor_id, event_type,
                    timestamp, zone_id, dwell_ms, is_staff, confidence, metadata
                ) VALUES (
                    :event_id, :store_id, :camera_id, :visitor_id, :event_type,
                    :timestamp, :zone_id, :dwell_ms, :is_staff, :confidence, :metadata
                )
                ON CONFLICT (event_id) DO NOTHING
            """), ev)

            # Upsert session
            await session.execute(text("""
                INSERT INTO sessions (session_id, store_id, visitor_id, entry_time, is_staff)
                VALUES (:session_id, :store_id, :visitor_id, :entry_time, :is_staff)
                ON CONFLICT (visitor_id) DO UPDATE SET
                    exit_time = CASE
                        WHEN :event_type = 'EXIT' THEN :timestamp
                        ELSE sessions.exit_time
                    END,
                    total_dwell_ms = sessions.total_dwell_ms + :dwell_ms,
                    updated_at = :timestamp
            """), {
                "session_id": str(uuid.uuid4()),
                "store_id":   ev["store_id"],
                "visitor_id": ev["visitor_id"],
                "entry_time": ev["timestamp"],
                "is_staff":   ev["is_staff"],
                "event_type": ev["event_type"],
                "timestamp":  ev["timestamp"],
                "dwell_ms":   ev["dwell_ms"],
            })

            # Update zones_visited for ZONE_ENTER events
            if ev["event_type"] == "ZONE_ENTER" and ev["zone_id"]:
                row = (await session.execute(text(
                    "SELECT zones_visited FROM sessions WHERE visitor_id = :vid"
                ), {"vid": ev["visitor_id"]})).fetchone()
                if row:
                    try:
                        zones_list = json.loads(row[0]) if row[0] else []
                    except (TypeError, ValueError):
                        zones_list = []
                    if ev["zone_id"] not in zones_list:
                        zones_list.append(ev["zone_id"])
                    await session.execute(text(
                        "UPDATE sessions SET zones_visited = :z WHERE visitor_id = :vid"
                    ), {"z": json.dumps(zones_list), "vid": ev["visitor_id"]})

        # 7. Link sessions to POS transactions
        print("Linking sessions to POS transactions...")
        for visitor_id, txn_id in session_to_txn.items():
            # Get session_id for this visitor
            row = (await session.execute(text(
                "SELECT session_id FROM sessions WHERE visitor_id = :vid"
            ), {"vid": visitor_id})).fetchone()
            if row:
                await session.execute(text("""
                    UPDATE pos_transactions
                    SET session_id = :sid
                    WHERE transaction_id = :txn_id
                """), {"sid": row[0], "txn_id": txn_id})
                await session.execute(text("""
                    UPDATE sessions SET converted = 1
                    WHERE session_id = :sid
                """), {"sid": row[0]})

        await session.commit()

    await engine.dispose()

    # Summary
    total_visitors = len(pos_rows) + n_browsers + 3
    print()
    print("=" * 50)
    print("✓ Real data seeded successfully!")
    print(f"  Store        : {STORE_ID} — Brigade Road, Bangalore")
    print(f"  Date         : {DATE_STR}")
    print(f"  Transactions : {len(pos_rows)}")
    print(f"  Total events : {len(all_events)}")
    print(f"  Sessions     : {total_visitors} ({len(pos_rows)} buyers + {n_browsers} browsers + 3 staff)")
    print(f"  Conv. rate   : ~{len(pos_rows)/total_visitors*100:.1f}%")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Seed real Brigade Road data into the database")
    parser.add_argument("--db-url", default=os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://storeuser:storepass@localhost:5432/store_intelligence"
    ))
    args = parser.parse_args()
    asyncio.run(seed(args.db_url))


if __name__ == "__main__":
    main()
