"""
Tests for the Detection Pipeline components

# PROMPT:
Generate pytest tests for the pipeline modules: tracker, reid, staff_detector,
zone_classifier, and emit. Cover: tracker assigns consistent IDs, reid detects
re-entry, staff detector flags purple uniforms, zone classifier assigns zones,
and emitter writes valid JSONL.

# CHANGES MADE:
- Added test_staff_detector_non_purple: ensures non-staff not flagged
- Added test_reid_new_visitor_gets_unique_id: basic Re-ID sanity check
- Added test_emitter_writes_valid_jsonl: validates output file format
- Added test_zone_classifier_outside_zones: returns None for unknown centroid
- Used numpy for synthetic frames (no real video needed)
"""

from __future__ import annotations
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest


# ── Tracker tests ─────────────────────────────────────────────────────────────

def test_tracker_assigns_id_to_detection():
    from pipeline.tracker import ByteTracker
    tracker = ByteTracker()
    dets = np.array([[100, 100, 200, 300, 0.9]], dtype=np.float32)
    tracks = tracker.update(dets)
    assert len(tracks) == 1
    assert tracks[0].track_id >= 1


def test_tracker_consistent_id_across_frames():
    from pipeline.tracker import ByteTracker
    tracker = ByteTracker()
    dets = np.array([[100, 100, 200, 300, 0.9]], dtype=np.float32)
    tracks1 = tracker.update(dets)
    # Slightly moved detection
    dets2 = np.array([[105, 102, 205, 302, 0.88]], dtype=np.float32)
    tracks2 = tracker.update(dets2)
    assert tracks1[0].track_id == tracks2[0].track_id


def test_tracker_empty_frame():
    from pipeline.tracker import ByteTracker
    tracker = ByteTracker()
    tracks = tracker.update(np.zeros((0, 5), dtype=np.float32))
    assert tracks == []


def test_tracker_multiple_persons():
    from pipeline.tracker import ByteTracker
    tracker = ByteTracker()
    dets = np.array([
        [50,  50,  150, 250, 0.92],
        [300, 50,  400, 250, 0.88],
        [600, 50,  700, 250, 0.85],
    ], dtype=np.float32)
    tracks = tracker.update(dets)
    assert len(tracks) == 3
    ids = [t.track_id for t in tracks]
    assert len(set(ids)) == 3  # all unique


def test_tracker_group_entry_individual_ids():
    """Group of 3 entering together → 3 separate track IDs."""
    from pipeline.tracker import ByteTracker
    tracker = ByteTracker()
    # 3 people close together (simulating group entry)
    dets = np.array([
        [100, 100, 160, 280, 0.91],
        [155, 100, 215, 280, 0.89],
        [210, 100, 270, 280, 0.87],
    ], dtype=np.float32)
    tracks = tracker.update(dets)
    assert len(tracks) == 3


# ── Re-ID tests ───────────────────────────────────────────────────────────────

def test_reid_new_visitor_gets_unique_id():
    from pipeline.reid import ReIDEngine
    reid = ReIDEngine()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    ts = datetime.now(timezone.utc)
    vid1, is_reentry1 = reid.resolve(1, frame, (100, 100, 200, 300), "STORE_BLR_002", ts)
    vid2, is_reentry2 = reid.resolve(2, frame, (300, 100, 400, 300), "STORE_BLR_002", ts)
    assert vid1 != vid2
    assert not is_reentry1
    assert not is_reentry2


def test_reid_same_track_same_visitor():
    from pipeline.reid import ReIDEngine
    reid = ReIDEngine()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    ts = datetime.now(timezone.utc)
    vid1, _ = reid.resolve(1, frame, (100, 100, 200, 300), "STORE_BLR_002", ts)
    vid2, _ = reid.resolve(1, frame, (102, 101, 202, 301), "STORE_BLR_002", ts)
    assert vid1 == vid2


def test_reid_visitor_id_format():
    from pipeline.reid import ReIDEngine
    reid = ReIDEngine()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    ts = datetime.now(timezone.utc)
    vid, _ = reid.resolve(99, frame, (50, 50, 150, 250), "STORE_BLR_002", ts)
    assert vid.startswith("VIS_")
    assert len(vid) == 10  # VIS_ + 6 hex chars


# ── Staff detector tests ──────────────────────────────────────────────────────

def test_staff_detector_purple_uniform():
    """Solid purple frame → detected as staff."""
    from pipeline.staff_detector import is_staff
    import cv2
    # Create a frame that is entirely Purplle purple (HSV ~150, 200, 200)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    # BGR for purple: approximately (150, 0, 150)
    frame[:, :] = [150, 0, 150]
    bbox = (100, 100, 300, 400)
    result, ratio = is_staff(frame, bbox)
    assert result is True
    assert ratio > 0.0


def test_staff_detector_non_purple():
    """Blue frame → not detected as staff."""
    from pipeline.staff_detector import is_staff
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:, :] = [200, 50, 50]  # BGR blue
    bbox = (100, 100, 300, 400)
    result, ratio = is_staff(frame, bbox)
    assert result is False


def test_staff_detector_degenerate_bbox():
    """Zero-size bbox → returns False gracefully."""
    from pipeline.staff_detector import is_staff
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    result, ratio = is_staff(frame, (100, 100, 100, 100))
    assert result is False


# ── Zone classifier tests ─────────────────────────────────────────────────────

@pytest.fixture
def layout_file(tmp_path):
    layout = {
        "stores": {
            "STORE_BLR_002": {
                "cameras": {
                    "CAM_FLOOR_01": {
                        "type": "FLOOR",
                        "zones": [
                            {
                                "zone_id": "SKINCARE",
                                "polygon": [[0, 0], [320, 0], [320, 240], [0, 240]]
                            },
                            {
                                "zone_id": "MAKEUP",
                                "polygon": [[320, 0], [640, 0], [640, 240], [320, 240]]
                            },
                        ]
                    },
                    "CAM_ENTRY_01": {
                        "type": "ENTRY",
                        "zones": [
                            {
                                "zone_id": "ENTRY",
                                "polygon": [[0, 200], [640, 200], [640, 480], [0, 480]]
                            }
                        ]
                    }
                }
            }
        }
    }
    p = tmp_path / "store_layout.json"
    p.write_text(json.dumps(layout))
    return str(p)


def test_zone_classifier_assigns_skincare(layout_file):
    from pipeline.zone_classifier import ZoneClassifier
    clf = ZoneClassifier(layout_file)
    zone = clf.classify("STORE_BLR_002", "CAM_FLOOR_01", 160, 120)
    assert zone == "SKINCARE"


def test_zone_classifier_assigns_makeup(layout_file):
    from pipeline.zone_classifier import ZoneClassifier
    clf = ZoneClassifier(layout_file)
    zone = clf.classify("STORE_BLR_002", "CAM_FLOOR_01", 480, 120)
    assert zone == "MAKEUP"


def test_zone_classifier_outside_zones(layout_file):
    from pipeline.zone_classifier import ZoneClassifier
    clf = ZoneClassifier(layout_file)
    zone = clf.classify("STORE_BLR_002", "CAM_FLOOR_01", 160, 400)
    assert zone is None


def test_zone_classifier_unknown_camera(layout_file):
    from pipeline.zone_classifier import ZoneClassifier
    clf = ZoneClassifier(layout_file)
    zone = clf.classify("STORE_BLR_002", "CAM_UNKNOWN", 160, 120)
    assert zone is None


def test_zone_classifier_is_entry_camera(layout_file):
    from pipeline.zone_classifier import ZoneClassifier
    clf = ZoneClassifier(layout_file)
    assert clf.is_entry_camera("STORE_BLR_002", "CAM_ENTRY_01") is True
    assert clf.is_entry_camera("STORE_BLR_002", "CAM_FLOOR_01") is False


# ── Emitter tests ─────────────────────────────────────────────────────────────

def test_emitter_writes_valid_jsonl(tmp_path):
    from pipeline.emit import EventEmitter
    out = str(tmp_path / "events.jsonl")
    emitter = EventEmitter(output_path=out, api_url=None)
    ts = datetime.now(timezone.utc)

    emitter.emit_entry("STORE_BLR_002", "CAM_ENTRY_01", "VIS_test", ts, False, 0.9)
    emitter.emit_zone_enter("STORE_BLR_002", "CAM_FLOOR_01", "VIS_test",
                            "SKINCARE", ts, False, 0.88)
    emitter.emit_zone_dwell("STORE_BLR_002", "CAM_FLOOR_01", "VIS_test",
                            "SKINCARE", ts, 30000, False, 0.87)
    emitter.emit_exit("STORE_BLR_002", "CAM_ENTRY_01", "VIS_test", ts, False, 0.91)
    emitter.close()

    lines = Path(out).read_text().strip().split("\n")
    assert len(lines) == 4
    for line in lines:
        event = json.loads(line)
        assert "event_id" in event
        assert "visitor_id" in event
        assert "event_type" in event
        assert "confidence" in event


def test_emitter_session_seq_increments(tmp_path):
    from pipeline.emit import EventEmitter
    out = str(tmp_path / "events.jsonl")
    emitter = EventEmitter(output_path=out, api_url=None)
    ts = datetime.now(timezone.utc)

    emitter.emit_entry("STORE_BLR_002", "CAM_ENTRY_01", "VIS_seq", ts, False, 0.9)
    emitter.emit_zone_enter("STORE_BLR_002", "CAM_FLOOR_01", "VIS_seq",
                            "SKINCARE", ts, False, 0.88)
    emitter.close()

    lines = Path(out).read_text().strip().split("\n")
    seqs = [json.loads(l)["metadata"]["session_seq"] for l in lines]
    assert seqs == [1, 2]


def test_emitter_billing_queue_join_has_queue_depth(tmp_path):
    from pipeline.emit import EventEmitter
    out = str(tmp_path / "events.jsonl")
    emitter = EventEmitter(output_path=out, api_url=None)
    ts = datetime.now(timezone.utc)
    emitter.emit_billing_queue_join(
        "STORE_BLR_002", "CAM_BILLING_01", "VIS_q", ts, 4, False, 0.9
    )
    emitter.close()
    event = json.loads(Path(out).read_text().strip())
    assert event["metadata"]["queue_depth"] == 4
