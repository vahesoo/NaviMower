"""Persistent NaviMower Custom Area polygons and import helpers."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import uuid
from typing import Any

OPT_CUSTOM_AREAS = "custom_areas"


def normalize_polygon(raw: Any) -> list[list[float]] | None:
    """Return a clean open polygon in mower-local X/Y coordinates."""
    if not isinstance(raw, (list, tuple)):
        return None
    points: list[list[float]] = []
    for point in raw:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError):
            continue
        candidate = [x, y]
        if not points or candidate != points[-1]:
            points.append(candidate)
    if len(points) >= 2 and points[0] == points[-1]:
        points.pop()
    return points if len(points) >= 3 else None


def _canonical_polygon(raw: Any) -> tuple[tuple[float, float], ...] | None:
    """Canonicalize a polygon independent of start vertex and winding."""
    polygon = normalize_polygon(raw)
    if polygon is None:
        return None
    points = tuple((round(point[0], 4), round(point[1], 4)) for point in polygon)
    rotations: list[tuple[tuple[float, float], ...]] = []
    for sequence in (points, tuple(reversed(points))):
        rotations.extend(
            sequence[index:] + sequence[:index]
            for index in range(len(sequence))
        )
    return min(rotations) if rotations else None


def polygon_fingerprint(raw: Any) -> str | None:
    """Return a stable geometry fingerprint for map-revision comparisons."""
    canonical = _canonical_polygon(raw)
    if canonical is None:
        return None
    payload = json.dumps(canonical, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def find_new_polygons(before: Any, after: Any) -> list[list[list[float]]]:
    """Return polygons present after a map edit but absent before it."""
    before_fingerprints = {
        fingerprint
        for raw in (before or [])
        if (fingerprint := polygon_fingerprint(raw)) is not None
    }
    result: list[list[list[float]]] = []
    seen: set[str] = set()
    for raw in after or []:
        polygon = normalize_polygon(raw)
        fingerprint = polygon_fingerprint(polygon)
        if (
            polygon is None
            or fingerprint is None
            or fingerprint in before_fingerprints
            or fingerprint in seen
        ):
            continue
        seen.add(fingerprint)
        result.append(polygon)
    return result


def polygon_area_m2(raw: Any) -> float | None:
    """Return polygon area in the mower map's metre coordinate system."""
    polygon = normalize_polygon(raw)
    if polygon is None:
        return None
    cross_sum = 0.0
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(index + 1) % len(polygon)]
        cross_sum += x1 * y2 - x2 * y1
    return abs(cross_sum) / 2.0


def polygon_centroid(raw: Any) -> tuple[float, float] | None:
    """Return a display-only centroid for the detected polygon."""
    polygon = normalize_polygon(raw)
    if polygon is None:
        return None
    cross_sum = 0.0
    x_sum = 0.0
    y_sum = 0.0
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(index + 1) % len(polygon)]
        cross = x1 * y2 - x2 * y1
        cross_sum += cross
        x_sum += (x1 + x2) * cross
        y_sum += (y1 + y2) * cross
    if abs(cross_sum) <= 1e-9:
        return (
            sum(point[0] for point in polygon) / len(polygon),
            sum(point[1] for point in polygon) / len(polygon),
        )
    return (x_sum / (3.0 * cross_sum), y_sum / (3.0 * cross_sum))


@dataclass(frozen=True, slots=True)
class NavimowerCustomArea:
    """A virtual polygon owned by NaviMower, independent of the robot map."""

    area_id: str
    name: str
    polygon: tuple[tuple[float, float], ...]
    source: str = "navimow_off_limit_import"

    @property
    def slug(self) -> str:
        value = re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_")
        return value or "custom_area"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.area_id,
            "name": self.name,
            "polygon": [[x, y] for x, y in self.polygon],
            "source": self.source,
        }


def create_custom_area(name: str, polygon: Any) -> NavimowerCustomArea | None:
    """Create one persistent area from a detected temporary off-limit polygon."""
    normalized = normalize_polygon(polygon)
    clean_name = str(name or "").strip()
    if normalized is None or not clean_name:
        return None
    return NavimowerCustomArea(
        area_id=uuid.uuid4().hex,
        name=clean_name,
        polygon=tuple((point[0], point[1]) for point in normalized),
    )


def parse_custom_areas(raw: Any) -> list[NavimowerCustomArea]:
    """Parse stored Custom Areas while rejecting malformed or duplicate rows."""
    if raw in (None, "", []):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []

    result: list[NavimowerCustomArea] = []
    seen_ids: set[str] = set()
    seen_slugs: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        polygon = normalize_polygon(item.get("polygon"))
        name = str(item.get("name") or "").strip()
        if polygon is None or not name:
            continue
        area_id = str(item.get("id") or polygon_fingerprint(polygon) or "").strip()
        source = str(item.get("source") or "navimow_off_limit_import")
        area = NavimowerCustomArea(
            area_id=area_id,
            name=name,
            polygon=tuple((point[0], point[1]) for point in polygon),
            source=source,
        )
        if not area_id or area_id in seen_ids or area.slug in seen_slugs:
            continue
        seen_ids.add(area_id)
        seen_slugs.add(area.slug)
        result.append(area)
    return result
