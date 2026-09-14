"""Authoritative retained-vendor current-cycle rendering.

The retained Navimow path is preferred only for zones where a fresh vendor row
exists. MQTT/session history remains the fallback for other current-cycle zones.
This prevents the same zone from being rasterized twice from two slightly
different geometry sources while preserving zones that the vendor endpoint no
longer returns.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .const import SWATH_WIDTH_M
from .current_cycle_render import (
    CurrentCycleRenderManager,
    build_current_cycle_render_source,
)
from .session_svg import SESSION_SVG_ARCHIVE_VERSION, build_session_svg_archive
from .vendor_trail import (
    VendorTrailCurrentCycleRenderManager,
    build_vendor_render_source,
    current_vendor_rows,
    vendor_rows_revision,
)
from .zone_state import as_float, as_int, zone_id_for_point

_INSTALLED = False


def _mowing_width(snapshot: dict[str, Any]) -> float:
    width = as_float(snapshot.get("mowing_path_width_m"))
    if width is None or not 0.1 <= width <= 2.0:
        return SWATH_WIDTH_M
    return width


async def _current_cycle_source(
    manager: VendorTrailCurrentCycleRenderManager,
    map_zones: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the normal current-cycle source before source arbitration."""
    sessions: list[dict[str, Any]] = []
    for summary in manager.history.session_summaries(include_points=False):
        session_id = summary.get("id") if isinstance(summary, dict) else None
        if not session_id:
            continue
        payload = await manager.history.async_session_payload(str(session_id))
        if isinstance(payload, dict):
            sessions.append(payload)
    return build_current_cycle_render_source(sessions, map_zones)


def _point_zone_id(point: Any, map_zones: list[dict[str, Any]]) -> int | None:
    if not isinstance(point, list) or len(point) < 3:
        return None
    if len(point) > 7:
        zone_id = as_int(point[7])
        if zone_id is not None:
            return zone_id
    x = as_float(point[1])
    y = as_float(point[2])
    if x is None or y is None:
        return None
    return zone_id_for_point(x, y, map_zones)


def filter_current_cycle_source(
    source: dict[str, Any],
    excluded_zone_ids: set[int],
    map_zones: list[dict[str, Any]],
) -> dict[str, Any]:
    """Drop MQTT geometry for vendor-owned zones without joining gaps.

    Segment starts and zone changes remain hard boundaries. This is important
    because removing one vendor-owned zone from a multi-zone session must never
    connect the two neighbouring MQTT fragments with a synthetic mowing edge.
    """
    starts = {
        value
        for value in (as_int(item) for item in source.get("segment_starts_ms") or [])
        if value is not None
    }
    segments: list[list[list[Any]]] = []
    current: list[list[Any]] = []
    current_zone: int | None = None

    def flush() -> None:
        nonlocal current
        if len(current) >= 2:
            segments.append(current)
        current = []

    for raw in source.get("points") or []:
        if not isinstance(raw, list) or len(raw) < 3:
            continue
        point = list(raw)
        stamp = as_int(point[0])
        zone_id = _point_zone_id(point, map_zones)

        if stamp in starts and current:
            flush()
            current_zone = None

        if zone_id in excluded_zone_ids:
            flush()
            current_zone = None
            continue

        if current and current_zone != zone_id:
            flush()
        current_zone = zone_id
        current.append(point)

    flush()

    points = [point for segment in segments for point in segment]
    segment_starts = [
        stamp
        for segment in segments
        if (stamp := as_int(segment[0][0])) is not None
    ]
    timestamps = [
        stamp
        for point in points
        if (stamp := as_int(point[0])) is not None
    ]
    retained_zone_ids = [
        zone_id
        for zone_id in (as_int(item) for item in source.get("zone_ids") or [])
        if zone_id is not None and zone_id not in excluded_zone_ids
    ]
    retained_zone_rows = [
        deepcopy(item)
        for item in source.get("current_cycle_zones") or []
        if isinstance(item, dict)
        and (zone_id := as_int(item.get("zone_id"))) is not None
        and zone_id not in excluded_zone_ids
    ]

    result = deepcopy(source)
    result["points"] = points
    result["segment_starts_ms"] = segment_starts
    result["zone_ids"] = retained_zone_ids
    result["current_cycle_zones"] = retained_zone_rows
    result["started_at_ms"] = min(timestamps) if timestamps else None
    result["ended_at_ms"] = max(timestamps) if timestamps else None
    return result


async def _authoritative_async_get(
    self: VendorTrailCurrentCycleRenderManager,
    map_zones: list[dict[str, Any]],
) -> dict[str, Any]:
    """Render each current-cycle zone from exactly one geometry source."""
    base = await CurrentCycleRenderManager.async_get(self, map_zones)
    snapshot = self.coordinator.data or {}
    cache = getattr(self.coordinator, "_vendor_trail_cache", {})
    rows = current_vendor_rows(snapshot, cache)
    vendor_revision = vendor_rows_revision(rows)
    if not vendor_revision:
        return base

    width = _mowing_width(snapshot)
    if vendor_revision != self._vendor_revision:
        vendor_source = build_vendor_render_source(
            rows,
            mowing_path_width_m=width,
        )
        vendor_artifact = None
        if len(vendor_source.get("points") or []) >= 2:
            vendor_artifact = await self.coordinator.hass.async_add_executor_job(
                build_session_svg_archive,
                vendor_source,
            )
        self._vendor_revision = vendor_revision
        self._vendor_artifact = vendor_artifact

    vendor_artifact = self._vendor_artifact
    vendor_area = (
        deepcopy(vendor_artifact.get("mowed_area"))
        if isinstance(vendor_artifact, dict)
        and isinstance(vendor_artifact.get("mowed_area"), dict)
        else None
    )
    vendor_path = str((vendor_area or {}).get("path_d") or "")
    if not vendor_path:
        return base

    vendor_zone_ids = {
        zone_id
        for row in rows
        if (zone_id := as_int(row.get("zone_id"))) is not None and zone_id > 0
    }
    fallback_key = (
        str(base.get("revision") or "0"),
        tuple(sorted(vendor_zone_ids)),
        round(width, 4),
    )
    if getattr(self, "_vendor_fallback_key", None) != fallback_key:
        source = await _current_cycle_source(self, map_zones)
        source = filter_current_cycle_source(source, vendor_zone_ids, map_zones)
        source["mowing_path_width_m"] = width
        fallback_artifact = None
        if len(source.get("points") or []) >= 2:
            fallback_artifact = await self.coordinator.hass.async_add_executor_job(
                build_session_svg_archive,
                source,
            )
        self._vendor_fallback_key = fallback_key
        self._vendor_fallback_artifact = fallback_artifact
        self._vendor_fallback_source = source

    fallback_artifact = getattr(self, "_vendor_fallback_artifact", None)
    fallback_source = getattr(self, "_vendor_fallback_source", {}) or {}
    fallback_area = (
        deepcopy(fallback_artifact.get("mowed_area"))
        if isinstance(fallback_artifact, dict)
        and isinstance(fallback_artifact.get("mowed_area"), dict)
        else None
    )
    fallback_path = str((fallback_area or {}).get("path_d") or "")

    combined_area = deepcopy(fallback_area or vendor_area or {})
    combined_area["path_d"] = f"{fallback_path}{vendor_path}"
    combined_area["fill_rule"] = "evenodd"
    combined_area["swath_width_m"] = width

    vendor_point_count = sum(as_int(row.get("point_count")) or 0 for row in rows)
    fallback_point_count = len(fallback_source.get("points") or [])
    fallback_zone_ids = [
        zone_id
        for zone_id in (as_int(item) for item in fallback_source.get("zone_ids") or [])
        if zone_id is not None and zone_id > 0
    ]
    combined_zone_ids = list(dict.fromkeys([*fallback_zone_ids, *sorted(vendor_zone_ids)]))

    result = deepcopy(base)
    result["mowed_area"] = combined_area
    result["revision"] = (
        f"{base.get('revision') or '0'}|vendor-authority:{vendor_revision}"
    )
    result["render_schema_version"] = (
        vendor_artifact.get("version")
        if isinstance(vendor_artifact, dict)
        else SESSION_SVG_ARCHIVE_VERSION
    )
    result["coordinate_space"] = "map_xy_m"
    result["zone_ids"] = combined_zone_ids
    result["source_point_count"] = fallback_point_count + vendor_point_count
    result["source"] = "vendor_retained_per_zone_with_mqtt_fallback"
    result["vendor_trail_debug"] = {
        "enabled": True,
        "authoritative": True,
        "mqtt_base_suppressed_for_zone_ids": sorted(vendor_zone_ids),
        "mqtt_fallback_zone_ids": fallback_zone_ids,
        "vendor_point_count": vendor_point_count,
        "revision": vendor_revision,
    }
    return result


def install_vendor_trail_render_semantics() -> None:
    """Install the per-zone source arbiter once."""
    global _INSTALLED
    if _INSTALLED:
        return
    VendorTrailCurrentCycleRenderManager.async_get = _authoritative_async_get
    _INSTALLED = True
