"""Local channel/corridor geometry used by gate automations and the map card."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any


def _normalize_polygon(raw: Any) -> tuple[tuple[float, float], ...] | None:
    """Return one validated open polygon in mower-local X/Y coordinates."""
    if raw in (None, "", []):
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raise ValueError("invalid polygon JSON") from None
    if not isinstance(raw, (list, tuple)):
        raise ValueError("polygon must be a coordinate list")

    points: list[tuple[float, float]] = []
    for point in raw:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            raise ValueError("polygon point must contain X and Y")
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError):
            raise ValueError("polygon coordinates must be numeric") from None
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("polygon coordinates must be finite")
        candidate = (x, y)
        if not points or candidate != points[-1]:
            points.append(candidate)

    if len(points) >= 2 and points[0] == points[-1]:
        points.pop()
    if len(points) < 3 or len(set(points)) < 3:
        raise ValueError("polygon needs at least three distinct points")
    return tuple(points)


def _point_on_segment(
    x: float,
    y: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    tolerance: float = 1e-6,
) -> bool:
    """Return whether a local-map point lies on one polygon edge."""
    cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
    if abs(cross) > tolerance:
        return False
    return (
        min(x1, x2) - tolerance <= x <= max(x1, x2) + tolerance
        and min(y1, y2) - tolerance <= y <= max(y1, y2) + tolerance
    )


def _point_in_polygon(
    x: float,
    y: float,
    polygon: tuple[tuple[float, float], ...],
) -> bool:
    """Return whether X/Y is inside or on the polygon boundary."""
    inside = False
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(index + 1) % len(polygon)]
        if _point_on_segment(x, y, x1, y1, x2, y2):
            return True
        if (y1 > y) == (y2 > y):
            continue
        crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
        if x < crossing_x:
            inside = not inside
    return inside


@dataclass(frozen=True, slots=True)
class NavimowerChannel:
    """A local-coordinate gate area, optionally using an exact polygon."""

    name: str
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    polygon: tuple[tuple[float, float], ...] | None = None

    @property
    def slug(self) -> str:
        value = re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_")
        return value or "channel"

    def contains(self, x: Any, y: Any) -> bool | None:
        """Return whether a valid live pose is inside this gate area."""
        try:
            px, py = float(x), float(y)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(px) or not math.isfinite(py):
            return None
        if self.polygon is not None:
            return _point_in_polygon(px, py, self.polygon)
        return self.x_min <= px <= self.x_max and self.y_min <= py <= self.y_max

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "x_min": self.x_min,
            "x_max": self.x_max,
            "y_min": self.y_min,
            "y_max": self.y_max,
            "slug": self.slug,
        }
        if self.polygon is not None:
            result["polygon"] = [[x, y] for x, y in self.polygon]
        return result


def parse_channels(raw: Any) -> list[NavimowerChannel]:
    """Parse saved gate areas with backward-compatible rectangle support.

    Legacy rows use ``x_min/x_max/y_min/y_max`` only. New rows may additionally
    provide ``polygon`` as a coordinate list (or JSON text). When present, the
    polygon is authoritative for membership and the rectangle fields are reduced
    to its bounding box for compatibility with older Map Card releases.
    """
    if raw in (None, "", []):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []

    channels: list[NavimowerChannel] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            name = str(item.get("name") or "Channel").strip()
            polygon = _normalize_polygon(item.get("polygon"))
            if polygon is not None:
                xs = [point[0] for point in polygon]
                ys = [point[1] for point in polygon]
                x_min, x_max = min(xs), max(xs)
                y_min, y_max = min(ys), max(ys)
            else:
                x1, x2 = float(item["x_min"]), float(item["x_max"])
                y1, y2 = float(item["y_min"]), float(item["y_max"])
                if not all(math.isfinite(value) for value in (x1, x2, y1, y2)):
                    raise ValueError("rectangle coordinates must be finite")
                x_min, x_max = min(x1, x2), max(x1, x2)
                y_min, y_max = min(y1, y2), max(y1, y2)
        except (KeyError, TypeError, ValueError):
            continue

        channel = NavimowerChannel(
            name=name or "Channel",
            x_min=x_min,
            x_max=x_max,
            y_min=y_min,
            y_max=y_max,
            polygon=polygon,
        )
        slug = channel.slug
        if slug in seen:
            continue
        seen.add(slug)
        channels.append(channel)
    return channels
