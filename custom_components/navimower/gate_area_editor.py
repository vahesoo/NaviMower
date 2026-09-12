"""Validated persistence helpers for Map Card gate-area editing."""
from __future__ import annotations

from typing import Any

from .channel import NavimowerChannel, parse_channels

MAX_GATE_AREA_POINTS = 64
MIN_GATE_AREA_M2 = 0.01
_EPSILON = 1e-9


def _signed_area(points: tuple[tuple[float, float], ...]) -> float:
    return 0.5 * sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])
    )


def _cross(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_on_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    if abs(_cross(start, end, point)) > _EPSILON:
        return False
    return (
        min(start[0], end[0]) - _EPSILON <= point[0] <= max(start[0], end[0]) + _EPSILON
        and min(start[1], end[1]) - _EPSILON <= point[1] <= max(start[1], end[1]) + _EPSILON
    )


def _segments_intersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    c1 = _cross(a1, a2, b1)
    c2 = _cross(a1, a2, b2)
    c3 = _cross(b1, b2, a1)
    c4 = _cross(b1, b2, a2)

    if ((c1 > _EPSILON and c2 < -_EPSILON) or (c1 < -_EPSILON and c2 > _EPSILON)) and (
        (c3 > _EPSILON and c4 < -_EPSILON) or (c3 < -_EPSILON and c4 > _EPSILON)
    ):
        return True

    return (
        (abs(c1) <= _EPSILON and _point_on_segment(b1, a1, a2))
        or (abs(c2) <= _EPSILON and _point_on_segment(b2, a1, a2))
        or (abs(c3) <= _EPSILON and _point_on_segment(a1, b1, b2))
        or (abs(c4) <= _EPSILON and _point_on_segment(a2, b1, b2))
    )


def _validate_simple_polygon(points: tuple[tuple[float, float], ...]) -> None:
    if len(points) > MAX_GATE_AREA_POINTS:
        raise ValueError(f"gate area supports at most {MAX_GATE_AREA_POINTS} points")
    if len(set(points)) != len(points):
        raise ValueError("gate area points must be unique")

    count = len(points)
    for first in range(count):
        first_next = (first + 1) % count
        for second in range(first + 1, count):
            second_next = (second + 1) % count
            if first == second or first_next == second or second_next == first:
                continue
            if _segments_intersect(
                points[first],
                points[first_next],
                points[second],
                points[second_next],
            ):
                raise ValueError("gate area polygon must not intersect itself")

    if abs(_signed_area(points)) < MIN_GATE_AREA_M2:
        raise ValueError("gate area polygon is too small")


def build_gate_area(name: Any, polygon: Any) -> NavimowerChannel:
    """Build one strict polygon gate area for dashboard/editor writes."""
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("gate area name is required")
    if len(clean_name) > 64:
        raise ValueError("gate area name must be 64 characters or fewer")
    if not isinstance(polygon, (list, tuple)):
        raise ValueError("gate area polygon must be a coordinate list")
    if len(polygon) > MAX_GATE_AREA_POINTS:
        raise ValueError(f"gate area supports at most {MAX_GATE_AREA_POINTS} points")

    parsed = parse_channels([{"name": clean_name, "polygon": polygon}])
    if len(parsed) != 1 or parsed[0].polygon is None:
        raise ValueError("gate area polygon needs at least three valid X/Y points")
    area = parsed[0]
    _validate_simple_polygon(area.polygon)
    return area


def upsert_gate_area(
    raw_channels: Any,
    *,
    gate_area_id: Any = None,
    name: Any,
    polygon: Any,
) -> list[dict[str, Any]]:
    """Create or replace one gate area and return canonical stored options."""
    channels = parse_channels(raw_channels)
    replacement = build_gate_area(name, polygon)
    existing_id = str(gate_area_id or "").strip().lower()

    if existing_id:
        index = next(
            (idx for idx, channel in enumerate(channels) if channel.slug == existing_id),
            None,
        )
        if index is None:
            raise ValueError(f"gate area '{existing_id}' was not found")
        if any(
            idx != index and channel.slug == replacement.slug
            for idx, channel in enumerate(channels)
        ):
            raise ValueError(f"gate area '{replacement.name}' already exists")
        channels[index] = replacement
    else:
        if any(channel.slug == replacement.slug for channel in channels):
            raise ValueError(f"gate area '{replacement.name}' already exists")
        channels.append(replacement)

    return [channel.as_dict() for channel in channels]


def delete_gate_area(raw_channels: Any, gate_area_id: Any) -> list[dict[str, Any]]:
    """Delete one gate area by its stable Map API slug."""
    existing_id = str(gate_area_id or "").strip().lower()
    if not existing_id:
        raise ValueError("gate_area_id is required")
    channels = parse_channels(raw_channels)
    remaining = [channel for channel in channels if channel.slug != existing_id]
    if len(remaining) == len(channels):
        raise ValueError(f"gate area '{existing_id}' was not found")
    return [channel.as_dict() for channel in remaining]
