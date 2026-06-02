"""
Zone Classifier - assigns a zone_id to a detection centroid.

Zones are defined in store_layout.json as polygons per camera.
We use point-in-polygon (ray-casting) for fast, deterministic assignment.
"""

import json
import numpy as np
from typing import Optional, Dict, Any, List, Tuple


def _point_in_polygon(px: float, py: float, polygon: List[Tuple[float, float]]) -> bool:
    """Ray-casting algorithm for point-in-polygon test."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi + 1e-9) + xi):
            inside = not inside
        j = i
    return inside


class ZoneClassifier:
    """
    Classifies a (cx, cy) centroid into a named zone for a given camera.

    store_layout.json expected structure:
    {
      "stores": {
        "STORE_BLR_002": {
          "cameras": {
            "CAM_ENTRY_01": {
              "zones": [
                {
                  "zone_id": "ENTRY",
                  "polygon": [[x1,y1],[x2,y2],...]
                }
              ]
            }
          }
        }
      }
    }
    """

    def __init__(self, layout_path: str):
        with open(layout_path, "r") as f:
            raw = json.load(f)

        # Support both top-level "stores" key and flat layout
        if "stores" in raw:
            self._layout: Dict[str, Any] = raw["stores"]
        else:
            self._layout = raw

    def classify(
        self,
        store_id: str,
        camera_id: str,
        cx: float,
        cy: float,
    ) -> Optional[str]:
        """
        Return the zone_id for centroid (cx, cy) or None if outside all zones.
        """
        try:
            cameras = self._layout[store_id]["cameras"]
            zones = cameras[camera_id]["zones"]
        except KeyError:
            return None

        for zone in zones:
            polygon = [tuple(p) for p in zone["polygon"]]
            if _point_in_polygon(cx, cy, polygon):
                return zone["zone_id"]

        return None

    def get_entry_zone_polygon(self, store_id: str, camera_id: str) -> Optional[List]:
        """Return the polygon for the ENTRY zone of a camera (used for direction detection)."""
        try:
            zones = self._layout[store_id]["cameras"][camera_id]["zones"]
            for z in zones:
                if z["zone_id"] in ("ENTRY", "EXIT", "ENTRY_EXIT"):
                    return z["polygon"]
        except KeyError:
            pass
        return None

    def is_entry_camera(self, store_id: str, camera_id: str) -> bool:
        """True if this camera covers the entry/exit threshold."""
        try:
            cam_meta = self._layout[store_id]["cameras"][camera_id]
            return cam_meta.get("type", "").upper() in ("ENTRY", "ENTRY_EXIT")
        except KeyError:
            return "ENTRY" in camera_id.upper()
