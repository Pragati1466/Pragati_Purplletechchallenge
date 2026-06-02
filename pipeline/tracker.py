"""
Multi-Object Tracker - wraps a simple IoU-based ByteTrack-style tracker.

We implement a lightweight ByteTrack variant in pure Python/NumPy so the
pipeline has no hard dependency on the upstream C++ ByteTrack repo.

Key design choices:
- High-confidence detections (conf >= track_thresh) are matched first.
- Low-confidence detections are used to recover lost tracks (ByteTrack idea).
- Tracks that are unmatched for > track_buffer frames are removed.
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from collections import defaultdict


def _iou(box_a: np.ndarray, box_b: np.ndarray) -> float:
    """Compute IoU between two [x1,y1,x2,y2] boxes."""
    xa = max(box_a[0], box_b[0])
    ya = max(box_a[1], box_b[1])
    xb = min(box_a[2], box_b[2])
    yb = min(box_a[3], box_b[3])
    inter = max(0, xb - xa) * max(0, yb - ya)
    if inter == 0:
        return 0.0
    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
    return inter / (area_a + area_b - inter + 1e-9)


def _iou_matrix(tracks: List["Track"], dets: np.ndarray) -> np.ndarray:
    """Return (n_tracks, n_dets) IoU matrix."""
    mat = np.zeros((len(tracks), len(dets)), dtype=np.float32)
    for i, t in enumerate(tracks):
        for j, d in enumerate(dets):
            mat[i, j] = _iou(t.bbox, d)
    return mat


def _greedy_match(cost: np.ndarray, threshold: float) -> List[Tuple[int, int]]:
    """
    Greedy matching: repeatedly pick the highest-cost pair above threshold.
    Returns list of (track_idx, det_idx) pairs.
    """
    matches = []
    cost = cost.copy()
    while True:
        if cost.size == 0:
            break
        idx = np.unravel_index(np.argmax(cost), cost.shape)
        val = cost[idx]
        if val < threshold:
            break
        matches.append(idx)
        cost[idx[0], :] = -1
        cost[:, idx[1]] = -1
    return matches


@dataclass
class Track:
    track_id: int
    bbox: np.ndarray          # [x1, y1, x2, y2]
    confidence: float
    age: int = 0              # frames since last match
    hits: int = 1
    history: List[np.ndarray] = field(default_factory=list)

    @property
    def centroid(self) -> Tuple[float, float]:
        return (
            float((self.bbox[0] + self.bbox[2]) / 2),
            float((self.bbox[1] + self.bbox[3]) / 2),
        )

    def update(self, bbox: np.ndarray, confidence: float) -> None:
        self.history.append(self.bbox.copy())
        self.bbox = bbox
        self.confidence = confidence
        self.age = 0
        self.hits += 1

    def predict(self) -> None:
        """Simple constant-velocity prediction (just age the track)."""
        self.age += 1


class ByteTracker:
    """
    Lightweight ByteTrack-style multi-object tracker.

    Args:
        track_thresh:  Confidence threshold for high-conf detections.
        track_buffer:  Max frames a track can be unmatched before removal.
        match_thresh:  IoU threshold for matching.
        frame_rate:    Video FPS (used to scale buffer).
    """

    def __init__(
        self,
        track_thresh: float = 0.5,
        track_buffer: int = 30,
        match_thresh: float = 0.8,
        frame_rate: int = 15,
    ):
        self.track_thresh = track_thresh
        self.track_buffer = track_buffer
        self.match_thresh = match_thresh
        self.frame_rate = frame_rate
        self._next_id = 1
        self.active_tracks: List[Track] = []
        self.lost_tracks: List[Track] = []

    def update(
        self,
        detections: np.ndarray,   # shape (N, 5): [x1,y1,x2,y2,conf]
    ) -> List[Track]:
        """
        Update tracker with new detections.
        Returns list of currently active tracks (with updated bboxes).
        """
        if len(detections) == 0:
            for t in self.active_tracks:
                t.predict()
            # Move aged-out tracks to lost
            still_active = []
            for t in self.active_tracks:
                if t.age <= self.track_buffer:
                    still_active.append(t)
                else:
                    self.lost_tracks.append(t)
            self.active_tracks = still_active
            return self.active_tracks

        high_dets = detections[detections[:, 4] >= self.track_thresh]
        low_dets  = detections[detections[:, 4] <  self.track_thresh]

        # --- Step 1: match high-conf dets to active tracks ---
        unmatched_tracks, unmatched_high = self._match(
            self.active_tracks, high_dets, self.match_thresh
        )

        # --- Step 2: match low-conf dets to unmatched active tracks ---
        remaining_tracks = [self.active_tracks[i] for i in unmatched_tracks]
        _, unmatched_low = self._match(remaining_tracks, low_dets, 0.5)

        # --- Step 3: age unmatched tracks ---
        for t in self.active_tracks:
            t.predict()

        # --- Step 4: create new tracks from unmatched high-conf dets ---
        for idx in unmatched_high:
            d = high_dets[idx]
            t = Track(
                track_id=self._next_id,
                bbox=d[:4].copy(),
                confidence=float(d[4]),
            )
            self._next_id += 1
            self.active_tracks.append(t)

        # --- Step 5: remove dead tracks ---
        still_active = []
        for t in self.active_tracks:
            if t.age <= self.track_buffer:
                still_active.append(t)
            else:
                self.lost_tracks.append(t)
        self.active_tracks = still_active

        return self.active_tracks

    def _match(
        self,
        tracks: List[Track],
        dets: np.ndarray,
        threshold: float,
    ) -> Tuple[List[int], List[int]]:
        """
        Match tracks to detections using IoU.
        Returns (unmatched_track_indices, unmatched_det_indices).
        """
        if len(tracks) == 0 or len(dets) == 0:
            return list(range(len(tracks))), list(range(len(dets)))

        iou_mat = _iou_matrix(tracks, dets[:, :4])
        matched_pairs = _greedy_match(iou_mat, threshold)

        matched_track_ids = set()
        matched_det_ids = set()
        for ti, di in matched_pairs:
            tracks[ti].update(dets[di, :4].copy(), float(dets[di, 4]))
            matched_track_ids.add(ti)
            matched_det_ids.add(di)

        unmatched_tracks = [i for i in range(len(tracks)) if i not in matched_track_ids]
        unmatched_dets   = [i for i in range(len(dets))   if i not in matched_det_ids]
        return unmatched_tracks, unmatched_dets

    def get_lost_tracks(self) -> List[Track]:
        return self.lost_tracks
