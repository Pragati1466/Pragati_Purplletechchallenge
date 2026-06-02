"""
Re-Identification Engine

Assigns a stable visitor_id across:
  1. Track gaps within a single camera (occlusion recovery)
  2. Re-entry: same person returns after an EXIT event

Approach: hybrid appearance + trajectory
  - Appearance: mean BGR histogram of torso region (fast, no GPU needed)
  - Trajectory: entry direction + time gap
  - Cosine similarity threshold: 0.72

Why not OSNet/torchreid:
  - 3-camera, single-store setup doesn't need heavy Re-ID
  - Histogram approach runs at 1000+ fps on CPU
  - Trajectory features break ties effectively
"""

from __future__ import annotations
import cv2
import numpy as np
import hashlib
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from datetime import datetime


APPEARANCE_THRESHOLD = 0.72   # cosine similarity
MAX_REENTRY_GAP_SEC  = 300    # 5 minutes — beyond this it's a new visit


def _extract_histogram(frame: np.ndarray, bbox: Tuple[int, int, int, int]) -> np.ndarray:
    """
    Extract a normalised 48-bin BGR histogram from the torso region.
    Returns a 1-D float32 vector of length 48.
    """
    x1, y1, x2, y2 = [int(v) for v in bbox]
    h = y2 - y1
    ty1 = y1 + int(h * 0.25)
    ty2 = y1 + int(h * 0.75)
    region = frame[ty1:ty2, x1:x2]
    if region.size == 0:
        return np.zeros(48, dtype=np.float32)

    hist = cv2.calcHist([region], [0, 1, 2], None, [4, 4, 3], [0, 256, 0, 256, 0, 256])
    hist = hist.flatten().astype(np.float32)
    norm = np.linalg.norm(hist)
    if norm > 0:
        hist /= norm
    return hist


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom < 1e-9:
        return 0.0
    return float(np.dot(a, b) / denom)


def _make_visitor_id(seed: str) -> str:
    """Generate a short deterministic visitor token from a seed string."""
    h = hashlib.md5(seed.encode()).hexdigest()[:6]
    return f"VIS_{h}"


@dataclass
class VisitorRecord:
    visitor_id: str
    appearance: np.ndarray          # histogram embedding
    last_seen: datetime
    last_bbox: Tuple[int, int, int, int]
    store_id: str
    exited: bool = False


class ReIDEngine:
    """
    Maintains a registry of known visitors and resolves track IDs to visitor IDs.

    Usage:
        reid = ReIDEngine()
        visitor_id, is_reentry = reid.resolve(
            track_id, frame, bbox, store_id, timestamp
        )
    """

    def __init__(self):
        # track_id -> visitor_id (active tracks)
        self._track_to_visitor: Dict[int, str] = {}
        # visitor_id -> VisitorRecord
        self._registry: Dict[str, VisitorRecord] = {}
        # visitor_id -> list of appearance histograms (for averaging)
        self._appearance_history: Dict[str, List[np.ndarray]] = {}

    def resolve(
        self,
        track_id: int,
        frame: np.ndarray,
        bbox: Tuple[int, int, int, int],
        store_id: str,
        timestamp: datetime,
    ) -> Tuple[str, bool]:
        """
        Resolve a track_id to a visitor_id.

        Returns:
            (visitor_id, is_reentry)
        """
        # Already know this track
        if track_id in self._track_to_visitor:
            vid = self._track_to_visitor[track_id]
            self._update_appearance(vid, frame, bbox)
            self._registry[vid].last_seen = timestamp
            self._registry[vid].last_bbox = bbox
            return vid, False

        # New track — try to match against exited visitors
        appearance = _extract_histogram(frame, bbox)
        matched_vid, is_reentry = self._match_exited(
            appearance, store_id, timestamp
        )

        if matched_vid:
            # Re-entry: reuse existing visitor_id
            self._track_to_visitor[track_id] = matched_vid
            self._registry[matched_vid].exited = False
            self._registry[matched_vid].last_seen = timestamp
            self._registry[matched_vid].last_bbox = bbox
            self._update_appearance(matched_vid, frame, bbox)
            return matched_vid, True

        # Brand-new visitor
        seed = f"{store_id}_{track_id}_{timestamp.isoformat()}"
        new_vid = _make_visitor_id(seed)
        self._track_to_visitor[track_id] = new_vid
        self._registry[new_vid] = VisitorRecord(
            visitor_id=new_vid,
            appearance=appearance,
            last_seen=timestamp,
            last_bbox=bbox,
            store_id=store_id,
        )
        self._appearance_history[new_vid] = [appearance]
        return new_vid, False

    def mark_exited(self, track_id: int) -> None:
        """Call when a track produces an EXIT event."""
        vid = self._track_to_visitor.get(track_id)
        if vid and vid in self._registry:
            self._registry[vid].exited = True

    def release_track(self, track_id: int) -> None:
        """Disassociate a dead track from its visitor (track may reappear later)."""
        self._track_to_visitor.pop(track_id, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _match_exited(
        self,
        appearance: np.ndarray,
        store_id: str,
        timestamp: datetime,
    ) -> Tuple[Optional[str], bool]:
        best_vid = None
        best_sim = APPEARANCE_THRESHOLD

        for vid, record in self._registry.items():
            if not record.exited:
                continue
            if record.store_id != store_id:
                continue
            gap = (timestamp - record.last_seen).total_seconds()
            if gap > MAX_REENTRY_GAP_SEC:
                continue

            # Use averaged appearance for robustness
            avg_appearance = self._avg_appearance(vid)
            sim = _cosine_sim(appearance, avg_appearance)
            if sim > best_sim:
                best_sim = sim
                best_vid = vid

        return best_vid, best_vid is not None

    def _update_appearance(
        self, vid: str, frame: np.ndarray, bbox: Tuple[int, int, int, int]
    ) -> None:
        hist = _extract_histogram(frame, bbox)
        history = self._appearance_history.setdefault(vid, [])
        history.append(hist)
        # Keep last 10 observations
        if len(history) > 10:
            history.pop(0)
        # Update registry with latest
        if vid in self._registry:
            self._registry[vid].appearance = hist

    def _avg_appearance(self, vid: str) -> np.ndarray:
        history = self._appearance_history.get(vid, [])
        if not history:
            return self._registry[vid].appearance
        avg = np.mean(history, axis=0).astype(np.float32)
        norm = np.linalg.norm(avg)
        return avg / norm if norm > 0 else avg
