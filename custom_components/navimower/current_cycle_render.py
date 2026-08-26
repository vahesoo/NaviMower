"""Backend-owned current mowing-cycle SVG render for the standalone map card.

The browser must not reconstruct mowing cycles from retained sessions.  This
module applies the same per-zone reset/completion boundary semantics used by the
integration's current-cycle trail model, keeps only completed fragments from the
latest cycle of each zone, and feeds those exact timestamped samples through the
existing session swath renderer.

Active-session points are intentionally not rasterized here.  The map API
already exposes the active live trail separately.  An active session can still
clear a previous zone cycle as soon as a confirmed reset boundary enters that
zone, so the default map never keeps an older completed swath underneath a new
cycle.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from .session_svg import SESSION_SVG_ARCHIVE_VERSION, build_session_svg_archive
from .zone_state import as_float, as_int, zone_id_for_point


def _unique_ints(values: Any) -> list[int]:
    result: list[int] = []
    for raw in values or []:
        value = as_int(raw)
        if value is not None and value > 0 and value not in result:
            result.append(value)
    return result


def _point_timestamp(point: Any) -> int | None:
    if not isinstance(point, list) or not point:
        return None
    return as_int(point[0])


def build_current_cycle_render_source(
    sessions: list[dict[str, Any]],
    map_zones: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return one synthetic completed session containing current-cycle samples.

    The selection deliberately mirrors ``zone_state.build_daily_trails``:

    * a completed/reset session becomes a boundary before the next cycle enters
      that zone;
    * explicit ``cycle_reset_zone_ids`` clear a zone on first entry;
    * in-session ``zone_cycle_boundaries`` clear a zone at the boundary sample;
    * interrupted/continued completed fragments accumulate until such a boundary;
    * active-session samples are not added, but their reset boundaries still
      remove the older completed cycle.

    Raw point rows are retained so the existing SVG renderer can continue to use
    MQTT cutting action/activity instead of treating every travelled edge as
    mowed grass.
    """
    by_zone: dict[int, dict[str, Any]] = {}
    boundary_before_next: set[int] = set()

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

        active = bool(session.get("active"))
        segment_starts = {
            value
            for value in (
                as_int(item) for item in session.get("segment_starts_ms") or []
            )
            if value is not None
        }
        explicit_reset_zones = set(_unique_ints(session.get("cycle_reset_zone_ids")))
        boundary_times: dict[int, list[int]] = {}
        for item in session.get("zone_cycle_boundaries") or []:
            if not isinstance(item, dict):
                continue
            zone_id = as_int(item.get("zone_id"))
            at_ms = as_int(item.get("at_ms"))
            if zone_id is not None and zone_id > 0 and at_ms is not None:
                boundary_times.setdefault(zone_id, []).append(at_ms)
        for values in boundary_times.values():
            values.sort()
        boundary_index = {zone_id: 0 for zone_id in boundary_times}

        encountered_zones: set[int] = set()
        current_zone: int | None = None
        current_segment: list[list[Any]] = []

        def flush() -> None:
            nonlocal current_segment
            if current_zone is not None and len(current_segment) >= 2 and not active:
                row = by_zone.get(current_zone)
                if row is None:
                    row = {
                        "zone_id": current_zone,
                        "cycle_id": session_id,
                        "segments": [],
                        "point_count": 0,
                    }
                    by_zone[current_zone] = row
                row["segments"].append([list(point) for point in current_segment])
                row["point_count"] += len(current_segment)
            current_segment = []

        for raw in points:
            if not isinstance(raw, list) or len(raw) < 3:
                continue
            stamp = as_int(raw[0])
            x = as_float(raw[1])
            y = as_float(raw[2])
            if stamp is None or x is None or y is None:
                continue

            zone_id = as_int(raw[7]) if len(raw) > 7 else None
            if zone_id is None:
                zone_id = zone_id_for_point(x, y, map_zones)
            if zone_id is None:
                flush()
                current_zone = None
                continue

            encountered_zones.add(zone_id)

            if zone_id in boundary_before_next or zone_id in explicit_reset_zones:
                flush()
                by_zone.pop(zone_id, None)
                boundary_before_next.discard(zone_id)
                explicit_reset_zones.discard(zone_id)
                current_zone = None

            values = boundary_times.get(zone_id) or []
            index = boundary_index.get(zone_id, 0)
            while index < len(values) and stamp >= values[index]:
                flush()
                by_zone.pop(zone_id, None)
                current_zone = None
                index += 1
            boundary_index[zone_id] = index

            fragment_boundary = stamp in segment_starts and bool(current_segment)
            if zone_id != current_zone or fragment_boundary:
                flush()
                current_zone = zone_id
            current_segment.append(list(raw))

        flush()

        reason = str(session.get("completion_reason") or "").lower()
        final_progress_zone_ids = {
            value
            for value in (
                as_int(item) for item in (session.get("final_progress") or {}).keys()
            )
            if value is not None and value > 0
        }
        if "reset" in reason or session.get("completed") is True:
            boundary_zones = (
                final_progress_zone_ids
                or encountered_zones
                or set(_unique_ints(session.get("zone_ids")))
            )
            boundary_before_next.update(boundary_zones)

    retained_segments: list[tuple[int, int, str, list[list[Any]]]] = []
    zone_rows: list[dict[str, Any]] = []
    for zone_id in sorted(by_zone):
        row = by_zone[zone_id]
        valid_segments = [
            segment
            for segment in row.get("segments") or []
            if isinstance(segment, list) and len(segment) >= 2
        ]
        if not valid_segments:
            continue
        point_count = sum(len(segment) for segment in valid_segments)
        zone_rows.append(
            {
                "zone_id": zone_id,
                "cycle_id": row.get("cycle_id"),
                "segment_count": len(valid_segments),
                "point_count": point_count,
            }
        )
        for segment in valid_segments:
            first_stamp = _point_timestamp(segment[0])
            if first_stamp is None:
                continue
            retained_segments.append(
                (first_stamp, zone_id, str(row.get("cycle_id") or ""), segment)
            )

    retained_segments.sort(key=lambda item: (item[0], item[1], item[2]))
    flattened: list[list[Any]] = []
    segment_starts_ms: list[int] = []
    for first_stamp, _zone_id, _cycle_id, segment in retained_segments:
        segment_starts_ms.append(first_stamp)
        flattened.extend(list(point) for point in segment)

    timestamps = [
        value for value in (_point_timestamp(point) for point in flattened) if value is not None
    ]
    return {
        "id": "current-cycle",
        "sequence": 0,
        "active": False,
        "started_at_ms": min(timestamps) if timestamps else None,
        "ended_at_ms": max(timestamps) if timestamps else None,
        "points": flattened,
        "segment_starts_ms": sorted(dict.fromkeys(segment_starts_ms)),
        "zone_ids": [row["zone_id"] for row in zone_rows],
        "current_cycle_zones": zone_rows,
    }


class CurrentCycleRenderManager:
    """Build and cache a compact backend current-cycle mowing swath."""

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator
        self.history = coordinator.history
        self._lock = asyncio.Lock()
        self._cache_key: tuple[Any, ...] | None = None
        self._cache: dict[str, Any] | None = None

    def _state_key(self) -> tuple[Any, ...]:
        cycle = self.history.cycle_diagnostics().get("last_event") or {}
        width = as_float((self.coordinator.data or {}).get("mowing_path_width_m"))
        return (
            self.history.active_session_no,
            as_int(cycle.get("at_ms")),
            str(cycle.get("reason") or ""),
            tuple(_unique_ints(cycle.get("zone_ids"))),
            repr(getattr(self.coordinator, "_map_cache_key", None)),
            width,
        )

    async def async_get(self, map_zones: list[dict[str, Any]]) -> dict[str, Any]:
        """Return the current-cycle swath without repeating CPU work per request."""
        key = self._state_key()
        if key == self._cache_key and self._cache is not None:
            return deepcopy(self._cache)

        async with self._lock:
            key = self._state_key()
            if key == self._cache_key and self._cache is not None:
                return deepcopy(self._cache)

            sessions: list[dict[str, Any]] = []
            for summary in self.history.session_summaries(include_points=False):
                session_id = summary.get("id") if isinstance(summary, dict) else None
                if not session_id:
                    continue
                payload = await self.history.async_session_payload(str(session_id))
                if isinstance(payload, dict):
                    sessions.append(payload)

            source = build_current_cycle_render_source(sessions, map_zones)
            width = as_float((self.coordinator.data or {}).get("mowing_path_width_m"))
            if width is not None and width > 0:
                source["mowing_path_width_m"] = width

            artifact = None
            if len(source.get("points") or []) >= 2:
                artifact = await self.coordinator.hass.async_add_executor_job(
                    build_session_svg_archive,
                    source,
                )

            mowed_area = (
                deepcopy(artifact.get("mowed_area"))
                if isinstance(artifact, dict)
                and isinstance(artifact.get("mowed_area"), dict)
                else {
                    "path_d": "",
                    "fill_rule": "evenodd",
                    "swath_width_m": width,
                    "grid_size_m": None,
                    "loop_count": 0,
                    "bbox": None,
                }
            )
            revision = ":".join(
                (
                    str(key[0] or 0),
                    str(key[1] or 0),
                    str(len(source.get("points") or [])),
                )
            )
            result = {
                "scope": "current_cycle",
                "revision": revision,
                "render_schema_version": (
                    artifact.get("version")
                    if isinstance(artifact, dict)
                    else SESSION_SVG_ARCHIVE_VERSION
                ),
                "coordinate_space": "map_xy_m",
                "zone_ids": list(source.get("zone_ids") or []),
                "zones": deepcopy(source.get("current_cycle_zones") or []),
                "source_point_count": len(source.get("points") or []),
                "mowed_area": mowed_area,
            }
            self._cache_key = key
            self._cache = deepcopy(result)
            return result
