"""Build compact SVG-ready render artifacts from completed mowing sessions.

The exact timestamped route remains in the normal session Store. This module
creates a derived, replaceable render cache: a filled mowing footprint for
cutting segments and a separate stroked path for travel/return segments.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
import math
from typing import Any, Iterable

from .const import (
    ACTIVITY_DOCKED,
    ACTIVITY_ERROR,
    ACTIVITY_MOWING,
    ACTIVITY_PAUSED,
    ACTIVITY_RETURNING,
    MAP_CARD_MIN_POINT_DISTANCE_M,
    MQTT_CUTTING_ACTIONS,
    SWATH_WIDTH_M,
)
from .zone_state import simplify_xy_points

SESSION_SVG_ARCHIVE_VERSION = 2
SESSION_SVG_GRID_M = 0.025
SESSION_SVG_MAX_ESTIMATED_CELLS = 1_500_000


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: float) -> str:
    rendered = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return "0" if rendered in {"-0", ""} else rendered


def _point_is_cutting(point: list[Any]) -> bool:
    """Classify one stored sample conservatively as blade-on or travel.

    MQTT action is the most specific signal. When it is unavailable, fall back
    to the normalized Home Assistant activity stored with the sample. Unknown
    states are treated as travel so the archive never invents mowed area.
    """
    action = _as_int(point[6]) if len(point) > 6 else None
    if action is not None:
        return action in MQTT_CUTTING_ACTIONS

    activity = str(point[4] if len(point) > 4 else "").strip().lower()
    if activity in {
        ACTIVITY_DOCKED,
        ACTIVITY_PAUSED,
        ACTIVITY_RETURNING,
        ACTIVITY_ERROR,
    }:
        return False
    return activity == ACTIVITY_MOWING or "mow" in activity or "cut" in activity


def _valid_points(
    session: dict[str, Any],
) -> list[tuple[int, float, float, bool, int | None]]:
    result: list[tuple[int, float, float, bool, int | None]] = []
    for raw in session.get("points") or []:
        if not isinstance(raw, list) or len(raw) < 3:
            continue
        stamp = _as_int(raw[0])
        x = _as_float(raw[1])
        y = _as_float(raw[2])
        if stamp is None or x is None or y is None:
            continue
        zone_id = _as_int(raw[7]) if len(raw) > 7 else None
        result.append((stamp, x, y, _point_is_cutting(raw), zone_id))
    return result


def _route_segments(
    session: dict[str, Any],
) -> tuple[list[list[list[float]]], list[list[list[float]]], list[list[list[float]]]]:
    """Return full, cutting-only and travel-only polyline fragments.

    An edge is considered mowed only when both endpoint samples are confirmed
    blade-on. Transition edges remain in the travel path, preserving dock,
    pause, return and zone-to-zone movement without falsely widening them into
    mowed footprint.
    """
    points = _valid_points(session)
    if len(points) < 2:
        return [], [], []

    starts = {
        value
        for value in (_as_int(item) for item in session.get("segment_starts_ms") or [])
        if value is not None
    }
    fragments: list[list[tuple[int, float, float, bool, int | None]]] = []
    current: list[tuple[int, float, float, bool, int | None]] = []
    for point in points:
        if current and point[0] in starts:
            fragments.append(current)
            current = []
        current.append(point)
    if current:
        fragments.append(current)

    all_segments: list[list[list[float]]] = []
    cutting_segments: list[list[list[float]]] = []
    travel_segments: list[list[list[float]]] = []

    for fragment in fragments:
        if len(fragment) < 2:
            continue
        all_segments.append([[item[1], item[2]] for item in fragment])
        kind: bool | None = None
        segment: list[list[float]] = []
        for previous, current_point in zip(fragment, fragment[1:]):
            edge_cutting = (
                previous[3]
                and current_point[3]
                and (previous[4] is not None or current_point[4] is not None)
            )
            start_xy = [previous[1], previous[2]]
            end_xy = [current_point[1], current_point[2]]
            if start_xy == end_xy:
                continue
            if kind is None or edge_cutting != kind:
                if len(segment) >= 2:
                    (cutting_segments if kind else travel_segments).append(segment)
                kind = edge_cutting
                segment = [start_xy, end_xy]
            else:
                if segment[-1] != start_xy:
                    segment.append(start_xy)
                segment.append(end_xy)
        if len(segment) >= 2 and kind is not None:
            (cutting_segments if kind else travel_segments).append(segment)

    return all_segments, cutting_segments, travel_segments


def _polyline_length(segments: Iterable[list[list[float]]]) -> float:
    total = 0.0
    for segment in segments:
        for first, second in zip(segment, segment[1:]):
            total += math.hypot(second[0] - first[0], second[1] - first[1])
    return total


def _adaptive_grid_size(
    cutting_segments: list[list[list[float]]],
    width_m: float = SWATH_WIDTH_M,
) -> float:
    length = _polyline_length(cutting_segments)
    estimated_area = max(width_m * length, width_m**2)
    needed = math.sqrt(estimated_area / SESSION_SVG_MAX_ESTIMATED_CELLS)
    return min(0.20, max(SESSION_SVG_GRID_M, needed))


def _distance_sq_to_segment(
    px: float,
    py: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> float:
    dx = x2 - x1
    dy = y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-18:
        return (px - x1) ** 2 + (py - y1) ** 2
    t = ((px - x1) * dx + (py - y1) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    qx = x1 + t * dx
    qy = y1 + t * dy
    return (px - qx) ** 2 + (py - qy) ** 2


def _rasterize_swath(
    cutting_segments: list[list[list[float]]],
    *,
    width_m: float = SWATH_WIDTH_M,
) -> tuple[set[tuple[int, int]], float]:
    cell = _adaptive_grid_size(cutting_segments, width_m)
    radius = max(0.01, float(width_m) / 2.0)
    # Cell-centre sampling keeps the stored footprint close to the requested
    # swath width. The adaptive grid always retains several cells across the
    # 0.25 m stroke, so connected route segments remain continuous.
    threshold = radius
    threshold_sq = threshold * threshold
    occupied: set[tuple[int, int]] = set()

    for segment in cutting_segments:
        for first, second in zip(segment, segment[1:]):
            x1, y1 = first
            x2, y2 = second
            min_ix = math.floor((min(x1, x2) - threshold) / cell)
            max_ix = math.floor((max(x1, x2) + threshold) / cell)
            min_iy = math.floor((min(y1, y2) - threshold) / cell)
            max_iy = math.floor((max(y1, y2) + threshold) / cell)
            for iy in range(min_iy, max_iy + 1):
                cy = (iy + 0.5) * cell
                for ix in range(min_ix, max_ix + 1):
                    cx = (ix + 0.5) * cell
                    if _distance_sq_to_segment(cx, cy, x1, y1, x2, y2) <= threshold_sq:
                        occupied.add((ix, iy))
    return occupied, cell


def _direction(edge: tuple[tuple[int, int], tuple[int, int]]) -> int:
    (x1, y1), (x2, y2) = edge
    dx, dy = x2 - x1, y2 - y1
    if (dx, dy) == (1, 0):
        return 0  # east
    if (dx, dy) == (0, 1):
        return 1  # north
    if (dx, dy) == (-1, 0):
        return 2  # west
    return 3  # south


def _boundary_loops(occupied: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Trace oriented outer and inner grid boundaries.

    Edges are oriented with occupied area on their left. At a diagonal corner,
    choosing the rightmost available continuation keeps touching components from
    being spuriously joined. SVG even-odd fill then preserves all interior holes.
    """
    edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for x, y in occupied:
        if (x, y - 1) not in occupied:
            edges.add(((x, y), (x + 1, y)))
        if (x + 1, y) not in occupied:
            edges.add(((x + 1, y), (x + 1, y + 1)))
        if (x, y + 1) not in occupied:
            edges.add(((x + 1, y + 1), (x, y + 1)))
        if (x - 1, y) not in occupied:
            edges.add(((x, y + 1), (x, y)))

    outgoing: dict[
        tuple[int, int],
        set[tuple[tuple[int, int], tuple[int, int]]],
    ] = defaultdict(set)
    for edge in edges:
        outgoing[edge[0]].add(edge)

    loops: list[list[tuple[int, int]]] = []
    remaining = set(edges)
    while remaining:
        first = min(remaining)
        remaining.remove(first)
        outgoing[first[0]].discard(first)
        start = first[0]
        current = first[1]
        incoming_dir = _direction(first)
        loop = [start, current]
        guard = 0
        while current != start and guard <= len(edges) + 4:
            candidates = [
                edge for edge in outgoing.get(current, set()) if edge in remaining
            ]
            if not candidates:
                break

            def rank(edge):
                delta = (_direction(edge) - incoming_dir) % 4
                # right, straight, left, reverse
                order = {3: 0, 0: 1, 1: 2, 2: 3}
                return (order[delta], edge[1])

            chosen = min(candidates, key=rank)
            remaining.remove(chosen)
            outgoing[current].discard(chosen)
            incoming_dir = _direction(chosen)
            current = chosen[1]
            loop.append(current)
            guard += 1
        if len(loop) >= 4 and loop[-1] == start:
            loops.append(_remove_collinear(loop))
    return loops


def _remove_collinear(loop: list[tuple[int, int]]) -> list[tuple[int, int]]:
    points = loop[:-1]
    if len(points) < 3:
        return loop
    result: list[tuple[int, int]] = []
    for index, current in enumerate(points):
        previous = points[index - 1]
        following = points[(index + 1) % len(points)]
        if (current[0] - previous[0]) * (following[1] - current[1]) == (
            current[1] - previous[1]
        ) * (following[0] - current[0]):
            continue
        result.append(current)
    if len(result) < 3:
        result = points
    return [*result, result[0]]


def _point_line_distance(point, first, second) -> float:
    px, py = point
    x1, y1 = first
    x2, y2 = second
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    return abs(dy * px - dx * py + x2 * y1 - y2 * x1) / math.hypot(dx, dy)


def _rdp(
    points: list[tuple[float, float]],
    tolerance: float,
) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points
    first, last = points[0], points[-1]
    best_distance = -1.0
    best_index = -1
    for index, point in enumerate(points[1:-1], start=1):
        distance = _point_line_distance(point, first, last)
        if distance > best_distance:
            best_distance = distance
            best_index = index
    if best_distance > tolerance and best_index > 0:
        left = _rdp(points[: best_index + 1], tolerance)
        right = _rdp(points[best_index:], tolerance)
        return left[:-1] + right
    return [first, last]


def _simplify_closed_loop(
    loop: list[tuple[int, int]], cell: float
) -> list[tuple[float, float]]:
    raw = [(x * cell, y * cell) for x, y in loop[:-1]]
    if len(raw) <= 4:
        return [*raw, raw[0]] if raw else []
    anchor_a = min(range(len(raw)), key=lambda i: (raw[i][0], raw[i][1]))
    ax, ay = raw[anchor_a]
    anchor_b = max(
        range(len(raw)),
        key=lambda i: (raw[i][0] - ax) ** 2 + (raw[i][1] - ay) ** 2,
    )
    if anchor_a > anchor_b:
        anchor_a, anchor_b = anchor_b, anchor_a
    first_half = raw[anchor_a : anchor_b + 1]
    second_half = raw[anchor_b:] + raw[: anchor_a + 1]
    tolerance = max(cell * 0.75, 0.035)
    simplified = _rdp(first_half, tolerance)[:-1] + _rdp(
        second_half, tolerance
    )[:-1]
    if len(simplified) < 3:
        simplified = raw
    return [*simplified, simplified[0]]


def _loops_path(loops: list[list[tuple[int, int]]], cell: float) -> str:
    parts: list[str] = []
    for loop in loops:
        points = _simplify_closed_loop(loop, cell)
        if len(points) < 4:
            continue
        parts.append(f"M{_fmt(points[0][0])} {_fmt(points[0][1])}")
        parts.extend(f"L{_fmt(x)} {_fmt(y)}" for x, y in points[1:-1])
        parts.append("Z")
    return "".join(parts)


def _polyline_path(segments: list[list[list[float]]]) -> tuple[str, int]:
    parts: list[str] = []
    rendered_points = 0
    for segment in segments:
        simplified = simplify_xy_points(
            segment,
            min_distance_m=MAP_CARD_MIN_POINT_DISTANCE_M,
        )
        if len(simplified) < 2:
            continue
        rendered_points += len(simplified)
        parts.append(f"M{_fmt(simplified[0][0])} {_fmt(simplified[0][1])}")
        parts.extend(f"L{_fmt(x)} {_fmt(y)}" for x, y in simplified[1:])
    return "".join(parts), rendered_points


def _bbox(segments: list[list[list[float]]]) -> list[float] | None:
    points = [point for segment in segments for point in segment]
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def session_render_fingerprint(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": str(session.get("id") or ""),
        "point_count": len(session.get("points") or []),
        "ended_at_ms": _as_int(session.get("ended_at_ms")),
        "segment_count": max(1, len(session.get("segment_starts_ms") or [])),
    }


def render_matches_session(render: Any, session: dict[str, Any]) -> bool:
    if not isinstance(render, dict):
        return False
    return (
        _as_int(render.get("version")) == SESSION_SVG_ARCHIVE_VERSION
        and render.get("source") == session_render_fingerprint(session)
    )


def build_session_svg_archive(session: dict[str, Any]) -> dict[str, Any] | None:
    """Return a compact SVG-ready archive for one completed session."""
    if session.get("active"):
        return None
    all_segments, cutting_segments, travel_segments = _route_segments(session)
    if not all_segments:
        return None

    swath_width = _as_float(session.get("mowing_path_width_m"))
    if swath_width is None or not 0.1 <= swath_width <= 2.0:
        swath_width = SWATH_WIDTH_M
    occupied, grid_size = _rasterize_swath(cutting_segments, width_m=swath_width)
    loops = _boundary_loops(occupied) if occupied else []
    mowed_path = _loops_path(loops, grid_size) if loops else ""
    travel_path, travel_points = _polyline_path(travel_segments)
    route_path, route_points = _polyline_path(all_segments)

    return {
        "version": SESSION_SVG_ARCHIVE_VERSION,
        "coordinate_space": "map_xy_m",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": session_render_fingerprint(session),
        "mowed_area": {
            "path_d": mowed_path,
            "fill_rule": "evenodd",
            "swath_width_m": swath_width,
            "grid_size_m": round(grid_size, 4),
            "loop_count": len(loops),
            "occupied_cell_count": len(occupied),
            "source_segment_count": len(cutting_segments),
            "bbox": _bbox(cutting_segments),
        },
        "travel": {
            "path_d": travel_path,
            "stroke_width_m": SWATH_WIDTH_M,
            "linecap": "round",
            "linejoin": "round",
            "source_segment_count": len(travel_segments),
            "render_point_count": travel_points,
            "bbox": _bbox(travel_segments),
        },
        # Full route path is retained as a compact fallback/debug representation.
        # Future cards normally combine mowed_area + travel instead.
        "route": {
            "path_d": route_path,
            "stroke_width_m": SWATH_WIDTH_M,
            "linecap": "round",
            "linejoin": "round",
            "source_segment_count": len(all_segments),
            "render_point_count": route_points,
            "bbox": _bbox(all_segments),
        },
    }
