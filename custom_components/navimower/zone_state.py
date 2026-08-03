"""Central per-zone progress model and daily trail preparation.

The integration owns all progress, area, cycle and route interpretation.  The
frontend receives already-normalized values and only renders them.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from typing import Any, Iterable

COMPLETION_THRESHOLD = 95


def as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clamp_pct(value: Any) -> float | None:
    parsed = as_float(value)
    if parsed is None:
        return None
    return max(0.0, min(100.0, parsed))


def _latest_iso(values: Iterable[Any]) -> str | None:
    known = [str(value) for value in values if value]
    return max(known) if known else None


def _point_in_polygon(x: float, y: float, polygon: Any) -> bool:
    if not isinstance(polygon, list) or len(polygon) < 3:
        return False
    inside = False
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        if not (
            isinstance(first, (list, tuple))
            and isinstance(second, (list, tuple))
            and len(first) >= 2
            and len(second) >= 2
        ):
            continue
        x1, y1 = as_float(first[0]), as_float(first[1])
        x2, y2 = as_float(second[0]), as_float(second[1])
        if None in (x1, y1, x2, y2):
            continue
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


def zone_id_for_point(x: float, y: float, map_zones: list[dict[str, Any]]) -> int | None:
    for zone in map_zones:
        zone_id = as_int(zone.get("id"))
        if zone_id is not None and _point_in_polygon(x, y, zone.get("polygon")):
            return zone_id
    return None


def build_zone_model(
    *,
    map_zones: list[dict[str, Any]],
    zone_details: list[dict[str, Any]],
    coverage: dict[str, Any] | None,
    zone_history: dict[str, dict[str, Any]],
    active_session: dict[str, Any] | None,
    active_zone_id: int | None,
    task_progress_pct: Any = None,
    task_mowed_area_m2: Any = None,
    task_progress_source: str | None = None,
    task_area_source: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return one authoritative state row per zone and weighted totals."""
    map_by_id = {
        as_int(item.get("id")): item
        for item in map_zones
        if isinstance(item, dict) and as_int(item.get("id")) is not None
    }
    detail_by_id = {
        as_int(item.get("id")): item
        for item in zone_details
        if isinstance(item, dict) and as_int(item.get("id")) is not None
    }
    coverage_by_id = {
        as_int(item.get("id")): item
        for item in (coverage or {}).get("zones") or []
        if isinstance(item, dict) and as_int(item.get("id")) is not None
    }
    zone_ids = sorted(
        value
        for value in set(map_by_id) | set(detail_by_id) | set(coverage_by_id)
        if value is not None
    )

    session = active_session or {}
    cycle_id = str(session.get("id")) if session.get("id") else None
    task_zone_ids = {
        value
        for value in (as_int(item) for item in session.get("zone_ids") or [])
        if value is not None
    }
    visited_zone_ids = {
        value
        for value in (as_int(item) for item in session.get("visited_zone_ids") or [])
        if value is not None
    }
    task_progress = session.get("task_zone_progress") or {}

    rows: list[dict[str, Any]] = []
    for zone_id in zone_ids:
        map_row = map_by_id.get(zone_id) or {}
        detail = detail_by_id.get(zone_id) or {}
        cloud = coverage_by_id.get(zone_id) or {}
        persisted = zone_history.get(str(zone_id)) or {}

        area = next(
            (
                value
                for value in (
                    as_float(detail.get("area_m2")),
                    as_float(map_row.get("area")),
                    as_float(cloud.get("area")),
                    as_float(persisted.get("area_m2")),
                )
                if value is not None and value >= 0
            ),
            None,
        )
        vendor_pct = clamp_pct(
            detail.get("vendor_percentage")
            if detail.get("vendor_percentage") is not None
            else detail.get("percentage")
            if detail.get("percentage") is not None
            else cloud.get("pct")
            if cloud.get("pct") is not None
            else persisted.get("vendor_percentage")
        )
        display_pct = clamp_pct(
            detail.get("progress")
            if detail.get("progress") is not None
            else detail.get("percentage")
            if detail.get("percentage") is not None
            else cloud.get("pct")
            if cloud.get("pct") is not None
            else persisted.get("percentage")
        )
        current_task_pct = clamp_pct(task_progress.get(str(zone_id)))
        task_override = (
            cycle_id is not None
            and zone_id in visited_zone_ids
            and current_task_pct is not None
        )
        if task_override:
            display_pct = current_task_pct

        raw_finished = next(
            (
                value
                for value in (
                    as_float(detail.get("finished_area_m2")),
                    as_float(cloud.get("finished")),
                    as_float(persisted.get("finished_area_m2")),
                )
                if value is not None and value >= 0
            ),
            None,
        )
        calculated = (
            area * display_pct / 100.0
            if area is not None and display_pct is not None
            else None
        )
        # Use the percentage-driven value whenever live progress has replaced the
        # slower cloud percentage.  Otherwise keep the vendor's exact finished m².
        progress_source = detail.get("progress_source") or persisted.get(
            "progress_source"
        )
        if calculated is not None and (
            task_override or progress_source not in (None, "coverage")
        ):
            mowed_area = calculated
        elif raw_finished is not None:
            mowed_area = raw_finished
        else:
            mowed_area = calculated
        if area is not None and mowed_area is not None:
            mowed_area = max(0.0, min(area, mowed_area))

        row_cycle = cycle_id if zone_id in visited_zone_ids else persisted.get("cycle_id")
        rows.append(
            {
                "id": zone_id,
                "name": (
                    detail.get("name")
                    or map_row.get("name")
                    or persisted.get("name")
                    or f"Zone {zone_id}"
                ),
                "area_m2": round(area, 2) if area is not None else None,
                "coverage_pct": round(display_pct, 1) if display_pct is not None else None,
                "mowed_area_m2": round(mowed_area, 2) if mowed_area is not None else None,
                "vendor_coverage_pct": round(vendor_pct, 1) if vendor_pct is not None else None,
                "task_progress_pct": (
                    round(current_task_pct, 1)
                    if current_task_pct is not None
                    else None
                ),
                "active": zone_id == active_zone_id,
                "selected_in_task": zone_id in task_zone_ids,
                "visited_in_task": zone_id in visited_zone_ids,
                "cycle_id": row_cycle,
                "last_started_at": (
                    detail.get("last_started_at")
                    or persisted.get("last_started_at")
                ),
                "last_mowed_at": detail.get("last_mowed_at") or persisted.get("last_mowed_at"),
                "last_completed_at": (
                    detail.get("last_completed_at")
                    or persisted.get("last_completed_at")
                ),
                "progress_source": (
                    progress_source
                    or ("task_cycle" if task_override else "coverage")
                ),
                "cutting_height_mm": detail.get("cutting_height_mm"),
                "stale": False,
            }
        )

    known_area = [row for row in rows if row.get("area_m2") is not None]
    map_area = sum(float(row["area_m2"]) for row in known_area)
    map_mowed = sum(float(row.get("mowed_area_m2") or 0.0) for row in known_area)
    map_coverage = 100.0 * map_mowed / map_area if map_area > 0 else None

    task_rows = [row for row in rows if row.get("selected_in_task")]
    task_area = sum(float(row.get("area_m2") or 0.0) for row in task_rows)

    # Per-zone progress is retained for zone entities and map markers. The
    # mower's own overall task percentage is a different counter and is the
    # authoritative Task progress whenever available. This prevents an active
    # zone/route counter from being mistaken for whole-task progress.
    weighted_task_mowed = 0.0
    for row in task_rows:
        area = as_float(row.get("area_m2")) or 0.0
        progress = as_float(row.get("task_progress_pct")) or 0.0
        weighted_task_mowed += area * progress / 100.0
    weighted_task_pct = (
        100.0 * weighted_task_mowed / task_area if task_area > 0 else None
    )

    direct_task_pct = clamp_pct(task_progress_pct)
    direct_task_mowed = as_float(task_mowed_area_m2)
    if direct_task_mowed is not None and direct_task_mowed < 0:
        direct_task_mowed = None
    if task_area > 0 and direct_task_mowed is not None:
        direct_task_mowed = min(task_area, direct_task_mowed)

    if direct_task_pct is not None:
        task_pct = direct_task_pct
        task_progress_resolved_source = task_progress_source or "vendor_overall"
        if direct_task_mowed is not None:
            task_mowed = direct_task_mowed
            task_area_resolved_source = task_area_source or "vendor_subtotal"
        else:
            task_mowed = task_area * direct_task_pct / 100.0 if task_area > 0 else None
            task_area_resolved_source = "task_progress_calculated"
    elif direct_task_mowed is not None and task_area > 0:
        task_mowed = direct_task_mowed
        task_pct = 100.0 * direct_task_mowed / task_area
        task_progress_resolved_source = task_area_source or "vendor_subtotal"
        task_area_resolved_source = task_area_source or "vendor_subtotal"
    else:
        task_mowed = weighted_task_mowed if task_area > 0 else None
        task_pct = weighted_task_pct
        task_progress_resolved_source = "area_weighted_zone_progress"
        task_area_resolved_source = "area_weighted_zone_progress"

    completed_values = [row.get("last_completed_at") for row in rows]
    last_map_completed = (
        max(str(value) for value in completed_values if value)
        if rows and all(completed_values)
        else None
    )
    totals = {
        "map_area_m2": round(map_area, 2) if map_area > 0 else None,
        "map_mowed_area_m2": round(map_mowed, 2) if map_area > 0 else None,
        "map_coverage_pct": round(map_coverage, 1) if map_coverage is not None else None,
        "task_area_m2": round(task_area, 2) if task_area > 0 else None,
        "task_mowed_area_m2": (
            round(task_mowed, 2) if task_mowed is not None else None
        ),
        "task_progress_pct": round(task_pct, 1) if task_pct is not None else None,
        "task_progress_source": task_progress_resolved_source,
        "task_mowed_area_source": task_area_resolved_source,
        "task_zone_progress_weighted_pct": (
            round(weighted_task_pct, 1) if weighted_task_pct is not None else None
        ),
        "task_zone_ids": sorted(task_zone_ids),
        "active_zone_id": active_zone_id,
        "zone_count": len(rows),
        "completed_zone_count": sum(
            1 for row in rows if (as_float(row.get("coverage_pct")) or 0) >= COMPLETION_THRESHOLD
        ),
        "last_map_mowed_at": _latest_iso(row.get("last_mowed_at") for row in rows),
        "last_map_completed_at": last_map_completed,
    }
    return rows, totals


def zone_model_signature(
    zones: list[dict[str, Any]], totals: dict[str, Any]
) -> tuple[Any, ...]:
    """Return the card-facing zone revision signature.

    Live ``last_mowed_at`` timestamps intentionally do not participate. They
    still update the HA sensor attributes, but must not force a static zone-layer
    rebuild for every retained mower position. Daily route changes have their own
    ``daily_trails_revision``.
    """
    return (
        tuple(
            (
                row.get("id"),
                row.get("name"),
                row.get("area_m2"),
                row.get("coverage_pct"),
                row.get("mowed_area_m2"),
                row.get("task_progress_pct"),
                row.get("active"),
                row.get("selected_in_task"),
                row.get("visited_in_task"),
                row.get("cycle_id"),
                row.get("last_started_at"),
                row.get("last_completed_at"),
            )
            for row in zones
        ),
        tuple(
            totals.get(key)
            for key in (
                "map_area_m2",
                "map_mowed_area_m2",
                "map_coverage_pct",
                "task_area_m2",
                "task_mowed_area_m2",
                "task_progress_pct",
                "task_progress_source",
                "task_mowed_area_source",
                "task_zone_progress_weighted_pct",
                "active_zone_id",
                "zone_count",
                "completed_zone_count",
                "last_map_completed_at",
            )
        ),
        tuple(totals.get("task_zone_ids") or []),
    )


def build_daily_trails(
    *,
    sessions: list[dict[str, Any]],
    map_zones: list[dict[str, Any]],
    local_date: date,
    to_local_date,
    revision: int,
) -> dict[str, Any]:
    """Keep only the latest same-day cycle trail for every mapped zone."""
    by_zone: dict[int, dict[str, Any]] = {}
    ordered = sorted(
        (item for item in sessions if isinstance(item, dict)),
        key=lambda item: as_int(item.get("started_at_ms")) or 0,
    )
    for session in ordered:
        session_id = str(session.get("id") or "")
        if not session_id:
            continue
        points = session.get("points") or []
        if not isinstance(points, list) or not points:
            continue
        starts = {
            value
            for value in (as_int(item) for item in session.get("segment_starts_ms") or [])
            if value is not None
        }
        encountered: set[int] = set()
        current_zone: int | None = None
        current_segment: list[list[float]] = []

        def flush() -> None:
            nonlocal current_segment
            if current_zone is not None and current_segment:
                by_zone[current_zone]["segments"].append(current_segment)
            current_segment = []

        for raw in points:
            if not isinstance(raw, list) or len(raw) < 3:
                continue
            stamp = as_int(raw[0])
            x, y = as_float(raw[1]), as_float(raw[2])
            if stamp is None or x is None or y is None or to_local_date(stamp) != local_date:
                flush()
                current_zone = None
                continue
            zone_id = as_int(raw[7]) if len(raw) > 7 else None
            if zone_id is None:
                zone_id = zone_id_for_point(x, y, map_zones)
            if zone_id is None:
                flush()
                current_zone = None
                continue
            if zone_id not in encountered:
                encountered.add(zone_id)
                by_zone[zone_id] = {
                    "zone_id": zone_id,
                    "cycle_id": session_id,
                    "active": bool(session.get("active")),
                    "segments": [],
                    "point_count": 0,
                }
            boundary = stamp in starts and current_segment
            if zone_id != current_zone or boundary:
                flush()
                current_zone = zone_id
            current_segment.append([x, y])
            by_zone[zone_id]["point_count"] += 1
        flush()

    result = []
    for zone_id in sorted(by_zone):
        row = deepcopy(by_zone[zone_id])
        row["segments"] = [segment for segment in row["segments"] if len(segment) >= 2]
        if row["segments"]:
            result.append(row)
    return {
        "date": local_date.isoformat(),
        "revision": revision,
        "zones": result,
    }
