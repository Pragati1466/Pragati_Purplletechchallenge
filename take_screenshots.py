"""
Takes 3 submission screenshots and saves them to Desktop.
Run after the API is up on port 8765.
"""
import asyncio
import subprocess
import json
import urllib.request

API = "http://localhost:8765"
TODAY = __import__('datetime').date.today().isoformat()


def seed_data():
    """Seed realistic events so screenshots show real data."""
    import uuid, datetime as dt, random

    base = dt.datetime.now(dt.timezone.utc).replace(
        hour=14, minute=0, second=0, microsecond=0
    )
    zones = ["FACES_CANADA", "GOOD_VIBES", "DERMDOC", "MINIMALIST",
             "MAYBELLINE", "ALPS_GOODNESS", "BILLING", "COSRX_KOREAN"]
    events = []

    for i in range(35):
        vid = f"VIS_{uuid.uuid4().hex[:6]}"
        t = base + dt.timedelta(minutes=i * 5)

        # ENTRY
        events.append({
            "event_id": str(uuid.uuid4()),
            "store_id": "ST1008",
            "camera_id": "CAM_ENTRY_01",
            "visitor_id": vid,
            "event_type": "ENTRY",
            "timestamp": t.isoformat().replace("+00:00", "Z"),
            "zone_id": None, "dwell_ms": 0,
            "is_staff": False, "confidence": round(random.uniform(0.82, 0.97), 2),
            "metadata": {"session_seq": 1},
        })

        # Zone visit
        zone = zones[i % len(zones)]
        cam = "CAM_BILLING_01" if zone == "BILLING" else "CAM_FLOOR_01"
        t2 = t + dt.timedelta(minutes=2)
        events.append({
            "event_id": str(uuid.uuid4()),
            "store_id": "ST1008",
            "camera_id": cam,
            "visitor_id": vid,
            "event_type": "ZONE_ENTER",
            "timestamp": t2.isoformat().replace("+00:00", "Z"),
            "zone_id": zone, "dwell_ms": 0,
            "is_staff": False, "confidence": round(random.uniform(0.78, 0.95), 2),
            "metadata": {"sku_zone": zone, "session_seq": 2},
        })

        # ZONE_DWELL
        dwell = random.randint(25000, 85000)
        t3 = t2 + dt.timedelta(seconds=30)
        events.append({
            "event_id": str(uuid.uuid4()),
            "store_id": "ST1008",
            "camera_id": cam,
            "visitor_id": vid,
            "event_type": "ZONE_DWELL",
            "timestamp": t3.isoformat().replace("+00:00", "Z"),
            "zone_id": zone, "dwell_ms": dwell,
            "is_staff": False, "confidence": round(random.uniform(0.75, 0.93), 2),
            "metadata": {"sku_zone": zone, "session_seq": 3},
        })

        # Billing queue join for some
        if i % 4 == 0:
            t4 = t + dt.timedelta(minutes=7)
            events.append({
                "event_id": str(uuid.uuid4()),
                "store_id": "ST1008",
                "camera_id": "CAM_BILLING_01",
                "visitor_id": vid,
                "event_type": "BILLING_QUEUE_JOIN",
                "timestamp": t4.isoformat().replace("+00:00", "Z"),
                "zone_id": "BILLING", "dwell_ms": 0,
                "is_staff": False, "confidence": 0.91,
                "metadata": {"queue_depth": random.randint(1, 4), "session_seq": 4},
            })

    # 3 staff events
    for i in range(3):
        events.append({
            "event_id": str(uuid.uuid4()),
            "store_id": "ST1008",
            "camera_id": "CAM_ENTRY_01",
            "visitor_id": f"STAFF_{i:03d}",
            "event_type": "ENTRY",
            "timestamp": (base + dt.timedelta(hours=1, minutes=i*30)).isoformat().replace("+00:00", "Z"),
            "zone_id": None, "dwell_ms": 0,
            "is_staff": True, "confidence": 0.94,
            "metadata": {"session_seq": 1},
        })

    # Send in batches
    for i in range(0, len(events), 50):
        batch = events[i:i+50]
        data = json.dumps({"events": batch}).encode()
        req = urllib.request.Request(
            f"{API}/events/ingest",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=10)

    print(f"Seeded {len(events)} events")


async def take_screenshots():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900})

        # ── Screenshot 1: Live Dashboard ──────────────────────────────────────
        print("Taking screenshot 1: Dashboard...")
        await page.goto(f"http://localhost:8765/dashboard", wait_until="networkidle")
        await page.wait_for_timeout(3000)  # let the auto-refresh populate data
        # Set store to ST1008
        await page.select_option("#store-select", "ST1008")
        await page.fill("#date-input", TODAY)
        await page.click("button:text('Refresh')")
        await page.wait_for_timeout(2500)
        await page.screenshot(
            path="/Users/apple/Desktop/screenshot1_dashboard.png",
            full_page=False
        )
        print("  Saved: screenshot1_dashboard.png")

        # ── Screenshot 2: API docs (OpenAPI) ──────────────────────────────────
        print("Taking screenshot 2: API docs...")
        await page.goto(f"http://localhost:8765/docs", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        await page.screenshot(
            path="/Users/apple/Desktop/screenshot2_api_docs.png",
            full_page=False
        )
        print("  Saved: screenshot2_api_docs.png")

        # ── Screenshot 3: Live metrics JSON response ───────────────────────────
        print("Taking screenshot 3: Metrics JSON...")
        await page.goto(
            f"http://localhost:8765/stores/ST1008/metrics?date={TODAY}",
            wait_until="networkidle"
        )
        await page.wait_for_timeout(1000)
        # Pretty-print the JSON on the page
        await page.evaluate("""() => {
            const pre = document.querySelector('pre') || document.body;
            pre.style.fontFamily = 'Monaco, monospace';
            pre.style.fontSize = '14px';
            pre.style.padding = '20px';
            pre.style.background = '#1a1030';
            pre.style.color = '#e2d9f3';
            document.body.style.background = '#1a1030';
        }""")
        await page.screenshot(
            path="/Users/apple/Desktop/screenshot3_metrics_api.png",
            full_page=False
        )
        print("  Saved: screenshot3_metrics_api.png")

        await browser.close()


if __name__ == "__main__":
    # Try to seed more data, ignore errors (may already have data)
    try:
        print("Seeding data...")
        seed_data()
    except Exception as e:
        print(f"  (seed skipped: {e})")
    print("Taking screenshots...")
    asyncio.run(take_screenshots())
    print()
    print("Done! Upload these 3 files:")
    print("  ~/Desktop/screenshot1_dashboard.png")
    print("  ~/Desktop/screenshot2_api_docs.png")
    print("  ~/Desktop/screenshot3_metrics_api.png")
