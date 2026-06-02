"""
Real-Time Event Simulator (Part E)

Replays a pre-generated events.jsonl file at configurable speed,
POSTing batches to the API to simulate a live detection pipeline.

Usage:
    python pipeline/simulate_realtime.py \
        --events output/events.jsonl \
        --api-url http://localhost:8000 \
        --speed 1.0
"""

from __future__ import annotations
import argparse
import json
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any

import httpx


def load_events(path: str) -> List[Dict[str, Any]]:
    events = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    # Sort by timestamp
    events.sort(key=lambda e: e["timestamp"])
    return events


def simulate(events: List[Dict[str, Any]], api_url: str, speed: float = 1.0) -> None:
    if not events:
        print("[simulate] No events to replay.")
        return

    print(f"[simulate] Replaying {len(events)} events at {speed}x speed → {api_url}")

    first_ts = datetime.fromisoformat(events[0]["timestamp"].replace("Z", "+00:00"))
    wall_start = time.time()
    batch: List[Dict[str, Any]] = []
    batch_size = 20

    for i, event in enumerate(events):
        event_ts = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        event_offset = (event_ts - first_ts).total_seconds()
        target_wall = wall_start + event_offset / speed

        # Sleep until it's time to send this event
        sleep_for = target_wall - time.time()
        if sleep_for > 0:
            time.sleep(sleep_for)

        batch.append(event)

        if len(batch) >= batch_size or i == len(events) - 1:
            _post_batch(api_url, batch)
            batch = []

    print("[simulate] Replay complete.")


def _post_batch(api_url: str, events: List[Dict[str, Any]]) -> None:
    payload = {"events": events}
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(f"{api_url}/events/ingest", json=payload)
            if resp.status_code == 200:
                data = resp.json()
                print(f"[simulate] Ingested {data.get('success', '?')} events")
            else:
                print(f"[simulate] WARNING: {resp.status_code} {resp.text[:100]}")
    except Exception as exc:
        print(f"[simulate] ERROR: {exc}")


def main():
    parser = argparse.ArgumentParser(description="Simulate real-time event stream")
    parser.add_argument("--events",  default="output/events.jsonl", help="Path to events.jsonl")
    parser.add_argument("--api-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--speed",   type=float, default=10.0,
                        help="Playback speed multiplier (default 10x = 2.3min clip in ~14s)")
    args = parser.parse_args()

    if not Path(args.events).exists():
        print(f"[simulate] Events file not found: {args.events}")
        print("[simulate] Run ./pipeline/run.sh first to generate events.")
        sys.exit(1)

    events = load_events(args.events)
    simulate(events, args.api_url, args.speed)


if __name__ == "__main__":
    main()
