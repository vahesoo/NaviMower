"""Zone-polygon constrained trail segment validation.

Vendor retained trail points are authoritative observations and are never removed
because two consecutive samples are far apart. Rendering decides only whether
the straight edge between them is geometrically plausible for that zone.

A segment is allowed when its centreline remains inside the zone polygon or
within a small tolerance outside its boundary. This preserves sparse compressed
vendor paths while rejecting phantom joins across buildings, concave cut-outs or
other unmapped space.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

ZONE_SEGMENT_TOLERANCE_M = 1.0
ZONE_SEGMENT_SAMPLE_STEP_M = 0.25
ZONE_SEGMENT_GUARD_VERSION = 3


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def normalize_polygon(raw: Any) -> list[list[float]]:
    if not isinstance(raw, (list, tuple)):
        return []
    points: list[list[float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        x, y = _number(item[0]), _number(item[1])
        if x is None or y is None:
            continue
        point = [x, y]
        if not points or points[-1] != point:
            points.append(point)
    if len(points) >= 2 and points[0] == points[-1]:
        points.pop()
    return points if len(points) >= 3 else []


def polygon_for_zone(map_zones: Any, zone_id: int) -> list[list[float]]:
    for row in map_zones or []:
        if not isinstance(row, dict):
            continue
        try:
            candidate = int(float(row.get("id")))
        except (TypeError, ValueError, OverflowError):
            continue
        if candidate == int(zone_id):
            return normalize_polygon(row.get("polygon"))
    return []


def polygon_signature(polygon: Any) -> str | None:
    points = normalize_polygon(polygon)
    if not points:
        return None
    payload = json.dumps(
        [[round(point[0], 3), round(point[1], 3)] for point in points],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def point_in_polygon(x: float, y: float, polygon: Any) -> bool:
    points = normalize_polygon(polygon)
    if not points:
        return False
    inside = False
    for index, first in enumerate(points):
        second = points[(index + 1) % len(points)]
        x1, y1 = first
        x2, y2 = second
        dx, dy = x2 - x1, y2 - y1
        cross = (x - x1) * dy - (y - y1) * dx
        if abs(cross) <= 1e-7:
            dot = (x - x1) * (x - x2) + (y - y1) * (y - y2)
            if dot <= 1e-7:
                return True
        if (y1 > y) != (y2 > y):
            at_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x <= at_x:
                inside = not inside
    return inside


def _distance_to_segment(
    x: float,
    y: float,
    first: list[float],
    second: list[float],
) -> float:
    x1, y1 = first
    x2, y2 = second
    dx, dy = x2 - x1, y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-18:
        return math.hypot(x - x1, y - y1)
    t = ((x - x1) * dx + (y - y1) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    qx, qy = x1 + t * dx, y1 + t * dy
    return math.hypot(x - qx, y - qy)


def distance_to_polygon(x: float, y: float, polygon: Any) -> float | None:
    points = normalize_polygon(polygon)
    if not points:
        return None
    if point_in_polygon(x, y, points):
        return 0.0
    return min(
        _distance_to_segment(x, y, points[index], points[(index + 1) % len(points)])
        for index in range(len(points))
    )


def point_within_zone_tolerance(
    x: float,
    y: float,
    polygon: Any,
    *,
    tolerance_m: float = ZONE_SEGMENT_TOLERANCE_M,
) -> bool:
    distance = distance_to_polygon(x, y, polygon)
    return distance is not None and distance <= max(0.0, float(tolerance_m))


def segment_within_zone_tolerance(
    first: Any,
    second: Any,
    polygon: Any,
    *,
    tolerance_m: float = ZONE_SEGMENT_TOLERANCE_M,
    sample_step_m: float = ZONE_SEGMENT_SAMPLE_STEP_M,
) -> bool:
    """Return whether the complete straight edge stays in/near the zone.

    Sampling is intentionally much denser than the 1 m tolerance. The work runs
    only when a sparse checkpoint/live-tail edge is considered and avoids a
    heavyweight geometry dependency.
    """
    if (
        not isinstance(first, (list, tuple))
        or not isinstance(second, (list, tuple))
        or len(first) < 2
        or len(second) < 2
    ):
        return False
    x1, y1 = _number(first[0]), _number(first[1])
    x2, y2 = _number(second[0]), _number(second[1])
    points = normalize_polygon(polygon)
    if None in (x1, y1, x2, y2) or not points:
        return False

    length = math.hypot(float(x2) - float(x1), float(y2) - float(y1))
    step = max(0.05, float(sample_step_m))
    samples = max(1, int(math.ceil(length / step)))
    for index in range(samples + 1):
        t = index / samples
        x = float(x1) + (float(x2) - float(x1)) * t
        y = float(y1) + (float(y2) - float(y1)) * t
        if not point_within_zone_tolerance(
            x,
            y,
            points,
            tolerance_m=tolerance_m,
        ):
            return False
    return True


def zone_guard_break_indices(
    points: list[Any],
    polygon: Any,
    *,
    tolerance_m: float = ZONE_SEGMENT_TOLERANCE_M,
) -> set[int]:
    """Return edge-start indices that must be split for polygon safety."""
    normalized = normalize_polygon(polygon)
    if not normalized:
        return set()
    breaks: set[int] = set()
    for index in range(1, len(points)):
        first = points[index - 1]
        second = points[index]
        if not segment_within_zone_tolerance(
            first,
            second,
            normalized,
            tolerance_m=tolerance_m,
        ):
            breaks.add(index)
    return breaks
