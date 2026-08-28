"""Integration-owned site metadata for future multi-mower map rendering."""
from __future__ import annotations

from typing import Any

from .georeference import (
    georeference_distance_m,
    georeference_is_valid,
    local_to_site_affine,
)

MULTI_MOWER_SITE_RADIUS_M = 500.0
MULTI_MOWER_SITE_SCHEMA_VERSION = 1


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _reference(georeference: Any) -> tuple[float, float] | None:
    if not georeference_is_valid(georeference):
        return None
    reference = georeference.get("reference") or {}
    latitude = _as_float(reference.get("latitude"))
    longitude = _as_float(reference.get("longitude"))
    if latitude is None or longitude is None:
        return None
    return latitude, longitude


def _iter_polygon_points(value: Any):
    if not isinstance(value, list):
        return
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        x = _as_float(point[0])
        y = _as_float(point[1])
        if x is not None and y is not None:
            yield x, y


def map_local_bounds(map_data: Any) -> dict[str, float] | None:
    """Return one conservative local X/Y box from already-decoded geometry."""
    if not isinstance(map_data, dict):
        return None
    points: list[tuple[float, float]] = []
    for zone in map_data.get("zones") or []:
        if isinstance(zone, dict):
            points.extend(_iter_polygon_points(zone.get("polygon")) or [])
    for key in ("off_limit_areas", "vf_off_areas"):
        for polygon in map_data.get(key) or []:
            points.extend(_iter_polygon_points(polygon) or [])
    for channel in map_data.get("channels") or []:
        if isinstance(channel, dict):
            points.extend(_iter_polygon_points(channel.get("points")) or [])
    station = map_data.get("station")
    if isinstance(station, dict):
        x = _as_float(station.get("x"))
        y = _as_float(station.get("y"))
        if x is not None and y is not None:
            points.append((x, y))
    if not points:
        return None
    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    return {
        "min_x": min_x,
        "min_y": min_y,
        "max_x": max_x,
        "max_y": max_y,
        "width": max_x - min_x,
        "height": max_y - min_y,
    }


def _transform_point(
    affine: dict[str, float], x: float, y: float
) -> tuple[float, float]:
    return (
        affine["a"] * x + affine["c"] * y + affine["e"],
        affine["b"] * x + affine["d"] * y + affine["f"],
    )


def _site_bounds(
    local_bounds: dict[str, float] | None,
    affine: dict[str, float] | None,
) -> dict[str, float] | None:
    if local_bounds is None or affine is None:
        return None
    corners = [
        (local_bounds["min_x"], local_bounds["min_y"]),
        (local_bounds["min_x"], local_bounds["max_y"]),
        (local_bounds["max_x"], local_bounds["min_y"]),
        (local_bounds["max_x"], local_bounds["max_y"]),
    ]
    transformed = [_transform_point(affine, x, y) for x, y in corners]
    min_east = min(point[0] for point in transformed)
    max_east = max(point[0] for point in transformed)
    min_north = min(point[1] for point in transformed)
    max_north = max(point[1] for point in transformed)
    return {
        "min_east": min_east,
        "max_east": max_east,
        "min_north": min_north,
        "max_north": max_north,
        "width": max_east - min_east,
        "height": max_north - min_north,
    }


def _svg_matrix(affine: dict[str, float] | None) -> list[float] | None:
    if affine is None:
        return None
    # SVG Y grows down while site North grows up. This matrix maps the mower's
    # existing local X/Y directly into a shared SVG site frame (east, -north).
    return [
        affine["a"],
        -affine["b"],
        affine["c"],
        -affine["d"],
        affine["e"],
        -affine["f"],
    ]


def _svg_bounds(site_bounds: dict[str, float] | None) -> dict[str, float] | None:
    if site_bounds is None:
        return None
    min_x = site_bounds["min_east"]
    max_x = site_bounds["max_east"]
    min_y = -site_bounds["max_north"]
    max_y = -site_bounds["min_north"]
    return {
        "min_x": min_x,
        "min_y": min_y,
        "max_x": max_x,
        "max_y": max_y,
        "width": max_x - min_x,
        "height": max_y - min_y,
    }


def _merge_site_bounds(bounds: list[dict[str, float]]) -> dict[str, float] | None:
    if not bounds:
        return None
    min_east = min(item["min_east"] for item in bounds)
    max_east = max(item["max_east"] for item in bounds)
    min_north = min(item["min_north"] for item in bounds)
    max_north = max(item["max_north"] for item in bounds)
    return {
        "min_east": min_east,
        "max_east": max_east,
        "min_north": min_north,
        "max_north": max_north,
        "width": max_east - min_east,
        "height": max_north - min_north,
    }


def build_site_payload(
    root_entry_id: str,
    coordinators: dict[str, Any],
    *,
    radius_m: float = MULTI_MOWER_SITE_RADIUS_M,
) -> dict[str, Any]:
    """Build one root-relative nearby-mower site descriptor.

    Only validated georeferences can be merged. Grouping is intentionally
    root-relative rather than transitive: a mower must be within ``radius_m`` of
    the card's anchor mower, so a chain of remote properties cannot accidentally
    become one giant site.
    """
    root = coordinators.get(root_entry_id)
    if root is None:
        raise KeyError(root_entry_id)
    root_data = root.data or {}
    root_georeference = root_data.get("georeference")
    root_reference = _reference(root_georeference)
    root_status = (
        str((root_georeference or {}).get("status") or "unavailable")
        if isinstance(root_georeference, dict)
        else "unavailable"
    )

    base = {
        "schema_version": MULTI_MOWER_SITE_SCHEMA_VERSION,
        "anchor_entry_id": root_entry_id,
        "radius_m": float(radius_m),
        "status": root_status,
        "multi_mower": False,
        "origin": None,
        "combined_site_bounds": None,
        "combined_svg_bounds": None,
        "members": [],
        "excluded_valid_count": 0,
        "unresolved_count": 0,
    }
    if root_reference is None:
        base["unresolved_count"] = sum(
            1
            for coordinator in coordinators.values()
            if not georeference_is_valid((coordinator.data or {}).get("georeference"))
        )
        return base

    origin_latitude, origin_longitude = root_reference
    base["status"] = "validated"
    base["origin"] = {
        "latitude": origin_latitude,
        "longitude": origin_longitude,
    }

    selected: list[tuple[float, str, Any, dict[str, Any]]] = []
    excluded_valid = 0
    unresolved = 0
    for entry_id, coordinator in coordinators.items():
        data = coordinator.data or {}
        georeference = data.get("georeference")
        if not georeference_is_valid(georeference):
            unresolved += 1
            continue
        distance = georeference_distance_m(root_georeference, georeference)
        if distance is None:
            unresolved += 1
            continue
        if distance > radius_m:
            excluded_valid += 1
            continue
        selected.append((distance, entry_id, coordinator, georeference))

    selected.sort(key=lambda item: (item[0], item[1]))
    site_bounds_items: list[dict[str, float]] = []
    members: list[dict[str, Any]] = []
    for distance, entry_id, coordinator, georeference in selected:
        data = coordinator.data or {}
        map_data = data.get("map") if isinstance(data.get("map"), dict) else {}
        affine = local_to_site_affine(
            georeference,
            origin_latitude,
            origin_longitude,
        )
        local_bounds = map_local_bounds(map_data)
        site_bounds = _site_bounds(local_bounds, affine)
        svg_bounds = _svg_bounds(site_bounds)
        if site_bounds is not None:
            site_bounds_items.append(site_bounds)
        members.append(
            {
                "entry_id": entry_id,
                "name": data.get("name") or coordinator.entry.title,
                "model": data.get("model") or coordinator.entry.data.get("model"),
                "vehicle_type": data.get("vehicle_type"),
                "distance_from_anchor_m": round(distance, 3),
                "map_revision": map_data.get("revision"),
                "georeference_source": georeference.get("source"),
                "georeference_status": georeference.get("status")
                or (georeference.get("validation") or {}).get("status"),
                "map_api_path": f"/api/navimower/map/{entry_id}",
                "local_to_site_en": affine,
                "svg_matrix": _svg_matrix(affine),
                "local_bounds": local_bounds,
                "site_bounds": site_bounds,
                "svg_bounds": svg_bounds,
            }
        )

    combined = _merge_site_bounds(site_bounds_items)
    base["members"] = members
    base["multi_mower"] = len(members) >= 2
    base["combined_site_bounds"] = combined
    base["combined_svg_bounds"] = _svg_bounds(combined)
    base["excluded_valid_count"] = excluded_valid
    base["unresolved_count"] = unresolved
    return base


__all__ = [
    "MULTI_MOWER_SITE_RADIUS_M",
    "MULTI_MOWER_SITE_SCHEMA_VERSION",
    "build_site_payload",
    "map_local_bounds",
]
