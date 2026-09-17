"""Authoritative retained-vendor current-cycle rendering.

VendorTrailStore retains the current-cycle SVG independently of polls and task
membership. Once owned, a zone cannot return to the History fallback until its
ZoneLedger cycle changes. History archives are never modified by this renderer.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .const import SWATH_WIDTH_M
from .current_cycle_render import (
    build_current_cycle_render_source,
)
from .session_svg import SESSION_SVG_ARCHIVE_VERSION, build_session_svg_archive
from .vendor_trail import (
    VendorTrailCurrentCycleRenderManager,
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
    ledger = manager.coordinator.vendor_trail_store.ledger
    # History session completion/selection is not a cycle boundary. Aggregate
    # completed fragments using only the ledger's confirmed per-zone cutoff.
    for session in sessions:
        session["completed"] = False
        session["completion_reason"] = ""
        session["cycle_reset_zone_ids"] = []
        session["zone_cycle_boundaries"] = []
        kept = []
        starts = set(session.get("segment_starts_ms") or [])
        gap = True
        for point in session.get("points") or []:
            zone_id = _point_zone_id(point, map_zones)
            row = ledger["zones"].get(str(zone_id)) or {}
            cutoff = as_int(row.get("cycle_started_at_ms")) or 0
            if (as_int(point[0]) or 0) < cutoff:
                gap = True
                continue
            if gap:
                starts.add(point[0])
            kept.append(point)
            gap = False
        session["points"] = kept
        session["segment_starts_ms"] = sorted(starts)
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


async def _authoritative_async_get(self, map_zones):
    """Do not publish an SVG built across a concurrent cycle reset/poll."""
    store = self.coordinator.vendor_trail_store
    def identity():
        return (store.revision, tuple((key, row.get("cycle_key")) for key, row in store.ledger["zones"].items()))
    async with self._lock:
        while True:
            before = identity()
            result = await _render_current_snapshot(self, map_zones)
            if identity() == before:
                return result


async def _render_current_snapshot(self, map_zones):
    store = self.coordinator.vendor_trail_store
    width = _mowing_width(self.coordinator.data or {})
    rows = await store.async_artifacts(width)
    owned = store.owned_zone_ids()
    # Reuse completed fallback fragments; vendor revisions never rebuild
    # unrelated zones or their SVGs.
    summaries = self.history.session_summaries(include_points=False)
    fallback_key = (
        repr([(row.get("id"), row.get("ended_at"), row.get("point_count"), row.get("active")) for row in summaries]),
        tuple(sorted(owned)),
        repr([(key, row.get("cycle_key")) for key, row in store.ledger["zones"].items()]),
        width,
    )
    if getattr(self, "_vendor_fallback_key", None) != fallback_key:
        source = await _current_cycle_source(self, map_zones)
        source = filter_current_cycle_source(source, owned, map_zones)
        source["mowing_path_width_m"] = width
        artifact = None
        if len(source.get("points") or []) >= 2:
            artifact = await self.coordinator.hass.async_add_executor_job(build_session_svg_archive, source)
        self._vendor_fallback_key = fallback_key
        self._vendor_fallback_artifact = artifact
        self._vendor_fallback_source = source

    source = getattr(self, "_vendor_fallback_source", {}) or {}
    fallback = getattr(self, "_vendor_fallback_artifact", None) or {}
    vendor_paths = [str(((row.get("artifact") or {}).get("mowed_area") or {}).get("path_d") or "") for row in rows]
    path = str((fallback.get("mowed_area") or {}).get("path_d") or "") + "".join(vendor_paths)
    fallback_ids = source.get("zone_ids") or []
    revisions = [(row["zone_id"], row["cycle_id"], row.get("artifact_revision")) for row in rows]
    import hashlib
    revision = hashlib.sha256(repr((fallback_key, revisions, path)).encode()).hexdigest()
    return {
        "scope": "current_cycle",
        "revision": revision,
        "render_schema_version": SESSION_SVG_ARCHIVE_VERSION,
        "coordinate_space": "map_xy_m",
        "zone_ids": sorted(set(fallback_ids) | owned),
        "zones": [{"zone_id": row["zone_id"], "cycle_id": row["cycle_id"],
                   "vendor_owned": True, "revision": row.get("geometry_revision")} for row in rows],
        "source_point_count": len(source.get("points") or []) + sum(len(row.get("points") or []) for row in rows),
        "mowed_area": {"path_d": path, "fill_rule": "evenodd", "swath_width_m": width},
        "source": "persistent_vendor_cycle_with_mqtt_fallback",
        "vendor_trail_debug": {
            "enabled": True, "authoritative": True,
            "mqtt_base_suppressed_for_zone_ids": sorted(owned),
            "mqtt_fallback_zone_ids": fallback_ids,
            "revision": store.revision,
        },
    }


def install_vendor_trail_render_semantics() -> None:
    """Install the per-zone source arbiter once."""
    global _INSTALLED
    if _INSTALLED:
        return
    VendorTrailCurrentCycleRenderManager.async_get = _authoritative_async_get
    _INSTALLED = True
