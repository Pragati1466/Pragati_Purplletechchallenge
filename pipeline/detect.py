"""
Main Detection Script
=====================
Processes a single CCTV clip and emits structured events.

Pipeline per frame:
  1. YOLOv8n detects persons (class 0)
  2. ByteTracker assigns consistent track IDs
  3. ReIDEngine maps track IDs → visitor IDs (handles re-entry)
  4. ZoneClassifier assigns zone_id from centroid
  5. StaffDetector flags is_staff via uniform colour
  6. EventEmitter writes events to JSONL + optionally POSTs to API

Edge cases handled (all 7 from spec):
  - Group entry:       NMS in YOLO ensures individual bboxes; each bbox → separate track
  - Staff movement:    HSV uniform colour detection; is_staff=True excluded from metrics
  - Re-entry:          ReIDEngine cosine-similarity match; REENTRY event emitted
  - Partial occlusion: conf_threshold=0.50 keeps low-conf detections; confidence logged
  - Empty periods:     No detections → no events (no false positives)
  - Billing queue:     Queue depth tracked per-frame; BILLING_QUEUE_JOIN emitted
  - Camera overlap:    Shared ReIDEngine across cameras deduplicates same visitor
"""

from __future__ import annotations
import argparse
import json
import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

import cv2
import numpy as np

# Allow running as a script from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.tracker import ByteTracker, Track
from pipeline.reid import ReIDEngine
from pipeline.zone_classifier import ZoneClassifier
from pipeline.staff_detector import is_staff as detect_staff
from pipeline.emit import EventEmitter

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False
    print("[detect] WARNING: ultralytics not installed. Using mock detections.")


# ── Constants ────────────────────────────────────────────────────────────────

PERSON_CLASS = 0
CONF_THRESHOLD = 0.50          # keep low to catch partial occlusions
IOU_THRESHOLD  = 0.45
DWELL_EMIT_INTERVAL_SEC = 30   # emit ZONE_DWELL every 30 s of continuous dwell
BILLING_ZONE_ID = "BILLING"


# ── Direction detection ───────────────────────────────────────────────────────

def _detect_direction(
    track: Track,
    entry_line_y: Optional[float],
) -> Optional[str]:
    """
    Determine if a track is moving INBOUND or OUTBOUND.

    We compare the centroid Y position across the last few frames.
    If entry_line_y is provided (from store_layout), we use crossing direction.
    Otherwise we fall back to vertical motion direction.
    """
    if len(track.history) < 3:
        return None

    prev_cy = (track.history[-3][1] + track.history[-3][3]) / 2
    curr_cy = (track.bbox[1] + track.bbox[3]) / 2

    if entry_line_y is not None:
        # Crossed the line downward → INBOUND (entering store)
        if prev_cy < entry_line_y <= curr_cy:
            return "INBOUND"
        if prev_cy > entry_line_y >= curr_cy:
            return "OUTBOUND"
        return None

    # Fallback: moving down = entering, moving up = exiting
    delta = curr_cy - prev_cy
    if abs(delta) < 5:
        return None
    return "INBOUND" if delta > 0 else "OUTBOUND"


# ── Per-track state ───────────────────────────────────────────────────────────

class TrackState:
    """Tracks zone dwell state for a single visitor."""

    def __init__(self):
        self.current_zone: Optional[str] = None
        self.zone_entry_time: Optional[datetime] = None
        self.last_dwell_emit: Optional[datetime] = None
        self.in_store: bool = False
        self.in_billing: bool = False
        self.billing_entry_time: Optional[datetime] = None


# ── Main processor ────────────────────────────────────────────────────────────

class ClipProcessor:
    """
    Processes a single video clip and emits events.

    Args:
        video_path:   Path to the video file.
        store_id:     Store identifier (from store_layout.json).
        camera_id:    Camera identifier.
        layout_path:  Path to store_layout.json.
        emitter:      Shared EventEmitter instance.
        reid:         Shared ReIDEngine instance (shared across cameras).
        clip_start_time: UTC datetime corresponding to frame 0.
        api_url:      Optional API URL for live streaming.
    """

    def __init__(
        self,
        video_path: str,
        store_id: str,
        camera_id: str,
        layout_path: str,
        emitter: EventEmitter,
        reid: ReIDEngine,
        clip_start_time: datetime,
        model_path: str = "yolov8n.pt",
    ):
        self.video_path = video_path
        self.store_id = store_id
        self.camera_id = camera_id
        self.emitter = emitter
        self.reid = reid
        self.clip_start_time = clip_start_time

        self.zone_clf = ZoneClassifier(layout_path)
        self.tracker = ByteTracker(
            track_thresh=CONF_THRESHOLD,
            track_buffer=30,
            match_thresh=0.8,
            frame_rate=15,
        )
        self.track_states: Dict[int, TrackState] = {}
        self.is_entry_cam = self.zone_clf.is_entry_camera(store_id, camera_id)

        # Entry line Y coordinate (midpoint of entry polygon, if available)
        entry_poly = self.zone_clf.get_entry_zone_polygon(store_id, camera_id)
        if entry_poly:
            ys = [p[1] for p in entry_poly]
            self.entry_line_y: Optional[float] = float(np.mean(ys))
        else:
            self.entry_line_y = None

        # Load YOLO model
        if YOLO_AVAILABLE:
            self.model = YOLO(model_path)
        else:
            self.model = None

    def process(self) -> int:
        """
        Process the clip. Returns total events emitted.

        Cross-camera deduplication: the shared ReIDEngine instance ensures
        that a visitor seen on CAM_ENTRY_01 and then on CAM_FLOOR_01 gets
        the same visitor_id — they are not double-counted as two visitors.
        """
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {self.video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
        frame_idx = 0
        events_emitted = 0

        # Billing queue depth: count of visitors currently in BILLING zone
        # Used to populate queue_depth in BILLING_QUEUE_JOIN events
        billing_occupancy: set = set()

        print(f"[detect] Processing {self.camera_id} @ {self.video_path}")

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            timestamp = self.clip_start_time + timedelta(seconds=frame_idx / fps)
            detections = self._detect(frame)
            active_tracks = self.tracker.update(detections)

            # Update billing occupancy for queue depth tracking
            current_billing: set = set()
            for track in active_tracks:
                cx, cy = track.centroid
                zone = self.zone_clf.classify(self.store_id, self.camera_id, cx, cy)
                if zone == BILLING_ZONE_ID:
                    current_billing.add(track.track_id)
            billing_occupancy = current_billing

            for track in active_tracks:
                n = self._process_track(
                    track, frame, timestamp,
                    queue_depth=len(billing_occupancy)
                )
                events_emitted += n

            frame_idx += 1

        cap.release()

        # Emit EXIT for any visitors still in store at clip end
        for tid, state in self.track_states.items():
            if state.in_store:
                vid = self.reid._track_to_visitor.get(tid)
                if vid:
                    self.emitter.emit_exit(
                        self.store_id, self.camera_id, vid,
                        self.clip_start_time + timedelta(seconds=frame_idx / fps),
                        False, 0.5,
                    )
                    events_emitted += 1

        self.emitter.flush()
        print(f"[detect] {self.camera_id}: {events_emitted} events emitted")
        return events_emitted

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect(self, frame: np.ndarray) -> np.ndarray:
        """Run YOLO detection. Returns (N,5) array [x1,y1,x2,y2,conf]."""
        if self.model is None:
            return np.zeros((0, 5), dtype=np.float32)

        results = self.model.predict(
            frame,
            classes=[PERSON_CLASS],
            conf=CONF_THRESHOLD,
            iou=IOU_THRESHOLD,
            verbose=False,
        )
        dets = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                dets.append([x1, y1, x2, y2, conf])
        return np.array(dets, dtype=np.float32) if dets else np.zeros((0, 5), dtype=np.float32)

    def _process_track(
        self, track: Track, frame: np.ndarray, timestamp: datetime,
        queue_depth: int = 0,
    ) -> int:
        """Process a single track for this frame. Returns events emitted."""
        tid = track.track_id
        bbox = tuple(int(v) for v in track.bbox)
        cx, cy = track.centroid
        conf = track.confidence

        # Resolve visitor ID (handles re-entry)
        visitor_id, is_reentry = self.reid.resolve(
            tid, frame, bbox, self.store_id, timestamp
        )

        # Staff detection
        staff_flag, _ = detect_staff(frame, bbox)

        # Initialise state for new tracks
        if tid not in self.track_states:
            self.track_states[tid] = TrackState()

        state = self.track_states[tid]
        events = 0

        # ── Entry / Exit (only on entry camera) ──────────────────────
        if self.is_entry_cam:
            direction = _detect_direction(track, self.entry_line_y)

            if direction == "INBOUND" and not state.in_store:
                state.in_store = True
                if is_reentry:
                    self.emitter.emit_reentry(
                        self.store_id, self.camera_id, visitor_id,
                        timestamp, staff_flag, conf,
                    )
                else:
                    self.emitter.emit_entry(
                        self.store_id, self.camera_id, visitor_id,
                        timestamp, staff_flag, conf,
                    )
                events += 1

            elif direction == "OUTBOUND" and state.in_store:
                state.in_store = False
                self.reid.mark_exited(tid)
                self.emitter.emit_exit(
                    self.store_id, self.camera_id, visitor_id,
                    timestamp, staff_flag, conf,
                )
                events += 1

        # ── Zone classification ───────────────────────────────────────
        zone_id = self.zone_clf.classify(self.store_id, self.camera_id, cx, cy)

        if zone_id != state.current_zone:
            # Zone exit
            if state.current_zone is not None and state.zone_entry_time:
                dwell_ms = int(
                    (timestamp - state.zone_entry_time).total_seconds() * 1000
                )
                self.emitter.emit_zone_exit(
                    self.store_id, self.camera_id, visitor_id,
                    state.current_zone, timestamp, dwell_ms, staff_flag, conf,
                )
                events += 1

                # Billing abandon check (no POS correlation here; API handles it)
                if state.current_zone == BILLING_ZONE_ID and state.in_billing:
                    self.emitter.emit_billing_queue_abandon(
                        self.store_id, self.camera_id, visitor_id,
                        timestamp, staff_flag, conf,
                    )
                    events += 1
                    state.in_billing = False

            # Zone enter
            if zone_id is not None:
                self.emitter.emit_zone_enter(
                    self.store_id, self.camera_id, visitor_id,
                    zone_id, timestamp, staff_flag, conf,
                )
                events += 1
                state.zone_entry_time = timestamp
                state.last_dwell_emit = timestamp

                # Billing queue join (queue depth computed per-frame above)
                if zone_id == BILLING_ZONE_ID:
                    state.in_billing = True
                    state.billing_entry_time = timestamp
                    self.emitter.emit_billing_queue_join(
                        self.store_id, self.camera_id, visitor_id,
                        timestamp, queue_depth=queue_depth,
                        is_staff=staff_flag, confidence=conf,
                    )
                    events += 1

            state.current_zone = zone_id

        # ── ZONE_DWELL (every 30 s of continuous dwell) ───────────────
        if (
            zone_id is not None
            and state.last_dwell_emit is not None
            and (timestamp - state.last_dwell_emit).total_seconds() >= DWELL_EMIT_INTERVAL_SEC
        ):
            dwell_ms = int(
                (timestamp - state.zone_entry_time).total_seconds() * 1000
            )
            self.emitter.emit_zone_dwell(
                self.store_id, self.camera_id, visitor_id,
                zone_id, timestamp, dwell_ms, staff_flag, conf,
            )
            state.last_dwell_emit = timestamp
            events += 1

        return events


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Process a CCTV clip and emit events")
    parser.add_argument("--video",      required=True,  help="Path to video file")
    parser.add_argument("--store-id",   required=True,  help="Store ID")
    parser.add_argument("--camera-id",  required=True,  help="Camera ID")
    parser.add_argument("--layout",     required=True,  help="Path to store_layout.json")
    parser.add_argument("--output",     default="output/events.jsonl", help="Output JSONL path")
    parser.add_argument("--api-url",    default=None,   help="API URL for live streaming")
    parser.add_argument("--model",      default="yolov8n.pt", help="YOLO model path")
    parser.add_argument("--start-time", default=None,
                        help="Clip start time ISO-8601 UTC (default: now)")
    args = parser.parse_args()

    if args.start_time:
        clip_start = datetime.fromisoformat(args.start_time).replace(tzinfo=timezone.utc)
    else:
        clip_start = datetime.now(timezone.utc)

    reid = ReIDEngine()
    emitter = EventEmitter(
        output_path=args.output,
        api_url=args.api_url,
        batch_size=50,
    )

    processor = ClipProcessor(
        video_path=args.video,
        store_id=args.store_id,
        camera_id=args.camera_id,
        layout_path=args.layout,
        emitter=emitter,
        reid=reid,
        clip_start_time=clip_start,
        model_path=args.model,
    )

    total = processor.process()
    emitter.close()
    print(f"[detect] Done. Total events: {total}")


if __name__ == "__main__":
    main()
