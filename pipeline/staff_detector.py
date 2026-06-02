"""
Staff Detector - Purplle uniform detection via HSV color analysis.

Purplle store staff wear a branded purple uniform.
We detect the dominant torso color and compare against the brand HSV range.
"""

import numpy as np
import cv2
from typing import Tuple


# Purplle brand purple HSV range (tuned for typical indoor lighting)
STAFF_LOWER_HSV = np.array([125, 40, 40])
STAFF_UPPER_HSV = np.array([175, 255, 255])
STAFF_RATIO_THRESHOLD = 0.28   # 28%+ of torso pixels must be purple


def extract_torso_region(frame: np.ndarray, bbox: Tuple[int, int, int, int]) -> np.ndarray:
    """
    Extract the torso region from a bounding box.
    We use the middle 40% vertically to avoid head/legs noise.
    """
    x1, y1, x2, y2 = bbox
    h = y2 - y1
    torso_y1 = y1 + int(h * 0.30)
    torso_y2 = y1 + int(h * 0.70)
    torso_x1 = x1 + int((x2 - x1) * 0.10)
    torso_x2 = x2 - int((x2 - x1) * 0.10)

    # Guard against degenerate boxes
    if torso_y2 <= torso_y1 or torso_x2 <= torso_x1:
        return np.zeros((1, 1, 3), dtype=np.uint8)

    return frame[torso_y1:torso_y2, torso_x1:torso_x2]


def is_staff(frame: np.ndarray, bbox: Tuple[int, int, int, int]) -> Tuple[bool, float]:
    """
    Determine whether the person in `bbox` is a Purplle staff member.

    Returns:
        (is_staff: bool, purple_ratio: float)
    """
    torso = extract_torso_region(frame, bbox)
    if torso.size == 0:
        return False, 0.0

    hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, STAFF_LOWER_HSV, STAFF_UPPER_HSV)
    purple_ratio = float(np.sum(mask > 0)) / float(mask.size)

    return purple_ratio >= STAFF_RATIO_THRESHOLD, round(purple_ratio, 4)
