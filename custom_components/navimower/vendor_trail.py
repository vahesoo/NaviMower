"""Vendor retained trail polling and temporary MQTT-tail debug helpers.

This beta module keeps the Navimow retained swept-path geometry separate from
MQTT history. Vendor geometry is used as an authoritative/recoverable backbone,
while the live card receives only the part of the MQTT trail that is still ahead
of the newest retained vendor point.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import UTC, datetime
import io
import json
import logging
import math
from typing import Any

from .const import SWATH_WIDTH_M
from .current_cycle_render import CurrentCycleRenderManager
from .session_svg import SESSION_SVG_ARCHIVE_VERSION, build_session_svg_archive

_LOGGER = logging.getLogger(__name__)

VENDOR_TRAIL_ACTIVE_TTL_SECONDS = 10
VENDOR_TRAIL_MATCH_RADIUS_M = 0.75


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


def _zstd_decompress(raw: bytes) -> bytes | None:
    """Decode a Navimow Zstandard frame using HA stdlib or package fallback."""
    try:
        import compression.zstd as _cz  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        _cz = None
    if _cz is not None:
        try:
            return _cz.decompress(raw)
        except Exception:  # noqa: BLE001
            try:
                return _cz.ZstdDecompressor().decompress(raw)
            except Exception:  # noqa: BLE001
                pass
    try:
        import zstandard  # type: ignore[import-not-found]

        return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)).read()
    except Exception:  # noqa: BLE001
        return None


def decode_vendor_trail_response(value: Any) -> list[dict[str, Any]]:
    """Return decoded rows from get-path-info-data-compress.

    The live endpoint currently returns Base64(Zstd(JSON)), but accepting an
    already-decoded list keeps the parser testable and tolerant of a future SDK
    wrapper that performs the transport decoding itself.
    """
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("data", "pathData", "path_data", "content"):
            if key in value:
                return decode_vendor_trail_response(value.get(key))
        return []
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        compressed = base64.b64decode(value.strip())
    except Exception as err:  # noqa: BLE001
        raise ValueError("vendor trail is not valid Base64") from err
    decoded = _zstd_decompress(compressed)
    if not decoded:
        raise ValueError("vendor trail Zstandard decoder unavailable or payload invalid")
    try:
        parsed = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ValueError("vendor trail decompressed payload is not JSON") from err
    return [dict(item) for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def normalize_vendor_trail_row(
    row: dict[str, Any],
    *,
    coverage: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Normalize one vendor row while trusting fresh path-info metadata first."""
    zone_id = _as_int(row.get("partitionId"))
    if zone_id is None or zone_id <= 0:
        return None
    points: list[list[Any]] = []
    for raw in row.get("points") or []:
        if not isinstance(raw, dict):
            continue
        x = _as_float(raw.get("x"))
        y = _as_float(raw.get("y"))
        if x is None or y is None:
            continue
        points.append(
            [
                round(x, 4),
                round(y, 4),
                str(raw.get("kr") or ""),
                str(raw.get("pt") or ""),
            ]
        )

    fresh = coverage or {}
    start_time = _as_int(fresh.get("start_time"))
    if start_time is None:
        start_time = _as_int(row.get("startTime"))
    end_time = _as_int(fresh.get("end_time"))
    if end_time is None:
        end_time = _as_int(row.get("endTime"))
    progress = _as_int(fresh.get("pct"))
    if progress is None:
        progress = _as_int(row.get("partitionPercentage"))

    last = points[-1][:2] if points else None
    signature = [
        zone_id,
        start_time or 0,
        progress if progress is not None else -1,
        len(points),
        last,
    ]
    return {
        "zone_id": zone_id,
        "start_time": start_time,
        "end_time": end_time,
        "progress": progress,
        "points": points,
        "point_count": len(points),
        "signature": signature,
    }


def task_zone_ids(snapshot: dict[str, Any]) -> list[int]:
    """Return the active/retained task zone set in stable order."""
    values: list[int] = []
    groups = [
        snapshot.get("current_zone_ids") or [],
        snapshot.get("target_zone_ids") or [],
        (snapshot.get("active_session") or {}).get("zone_ids") or [],
    ]
    for group in groups:
        for raw in group:
            zone_id = _as_int(raw)
            if zone_id is not None and zone_id > 0 and zone_id not in values:
                values.append(zone_id)
    if not values:
        for key in ("active_zone_progress_zone_id", "current_physical_zone_id"):
            zone_id = _as_int(snapshot.get(key))
            if zone_id is not None and zone_id > 0 and zone_id not in values:
                values.append(zone_id)
    return values


def coverage_by_zone(snapshot: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        zone_id: dict(item)
        for item in (snapshot.get("coverage") or {}).get("zones") or []
        if isinstance(item, dict)
        and (zone_id := _as_int(item.get("id"))) is not None
        and zone_id > 0
    }


def current_vendor_rows(
    snapshot: dict[str, Any],
    cache: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return cached rows that still match the fresh task/cycle metadata."""
    selected = set(task_zone_ids(snapshot))
    coverage = coverage_by_zone(snapshot)
    rows: list[dict[str, Any]] = []
    for zone_id in sorted(selected):
        cached = cache.get(zone_id)
        if not isinstance(cached, dict):
            continue
        fresh = coverage.get(zone_id) or {}
        fresh_start = _as_int(fresh.get("start_time"))
        cached_start = _as_int(cached.get("start_time"))
        if fresh_start is not None and cached_start is not None and fresh_start != cached_start:
            continue
        fresh_pct = _as_int(fresh.get("pct"))
        # Fresh path-info is the current-cycle authority. A 0% row must not
        # resurrect retained points whose compressed-row metadata is stale.
        if fresh_pct == 0:
            continue
        rows.append(cached)
    return rows


def active_vendor_row(
    snapshot: dict[str, Any],
    cache: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the retained row for the currently active physical/work zone.

    Full coordinator snapshots expose explicit active-zone IDs. The smaller Map
    API payload intentionally omits those private runtime aliases, but its public
    ``zone_states`` model retains the same resolved active flag. Supporting both
    shapes keeps multi-zone MQTT-tail trimming deterministic without adding a new
    frontend-only identity field.
    """
    candidate_ids: list[int] = []
    for key in ("active_zone_progress_zone_id", "current_physical_zone_id"):
        zone_id = _as_int(snapshot.get(key))
        if zone_id is not None and zone_id > 0 and zone_id not in candidate_ids:
            candidate_ids.append(zone_id)
    for state in snapshot.get("zone_states") or []:
        if not isinstance(state, dict) or state.get("active") is not True:
            continue
        zone_id = _as_int(state.get("id"))
        if zone_id is not None and zone_id > 0 and zone_id not in candidate_ids:
            candidate_ids.append(zone_id)

    rows = current_vendor_rows(snapshot, cache)
    by_id = {
        zone_id: row
        for row in rows
        if (zone_id := _as_int(row.get("zone_id"))) is not None
    }
    for zone_id in candidate_ids:
        if zone_id in by_id:
            return by_id[zone_id]
    return rows[0] if len(rows) == 1 else None


def _distance(a: list[float], b: list[float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _trail_distance(segments: list[list[list[float]]]) -> float:
    total = 0.0
    for segment in segments:
        for first, second in zip(segment, segment[1:]):
            total += _distance(first, second)
    return total


def trim_mqtt_tail_segments(
    segments: list[list[list[float]]],
    vendor_points: list[list[Any]],
    *,
    radius_m: float = VENDOR_TRAIL_MATCH_RADIUS_M,
) -> tuple[list[list[list[float]]], dict[str, Any]]:
    """Keep only the MQTT path at/after the newest retained vendor point.

    The match scans backwards through MQTT data so repeated passes near the same
    coordinates resolve to the newest plausible sample. One anchor point is kept
    even when the vendor has fully caught up; the card can use it to append MQTT
    positions arriving after the most recent map API refresh without resurrecting
    the already-confirmed older local trail.
    """
    clean = [
        [
            [float(point[0]), float(point[1])]
            for point in segment
            if isinstance(point, (list, tuple)) and len(point) >= 2
        ]
        for segment in segments or []
        if isinstance(segment, list)
    ]
    clean = [segment for segment in clean if segment]
    vendor_xy = [
        [float(point[0]), float(point[1])]
        for point in vendor_points or []
        if isinstance(point, (list, tuple)) and len(point) >= 2
    ]
    if not clean or not vendor_xy:
        return clean, {
            "matched": False,
            "match_distance_m": None,
            "mqtt_tail_point_count": sum(len(item) for item in clean),
            "mqtt_tail_distance_m": round(_trail_distance(clean), 2),
            "anchor_xy": None,
        }

    target = vendor_xy[-1]
    match: tuple[int, int, float] | None = None
    for segment_index in range(len(clean) - 1, -1, -1):
        segment = clean[segment_index]
        for point_index in range(len(segment) - 1, -1, -1):
            distance = _distance(segment[point_index], target)
            if distance <= radius_m:
                match = (segment_index, point_index, distance)
                break
        if match is not None:
            break

    if match is None:
        return clean, {
            "matched": False,
            "match_distance_m": None,
            "mqtt_tail_point_count": sum(len(item) for item in clean),
            "mqtt_tail_distance_m": round(_trail_distance(clean), 2),
            "anchor_xy": None,
        }

    segment_index, point_index, distance = match
    tail = [clean[segment_index][point_index:]] + clean[segment_index + 1 :]
    tail = [segment for segment in tail if segment]
    return tail, {
        "matched": True,
        "match_distance_m": round(distance, 3),
        "mqtt_tail_point_count": sum(len(item) for item in tail),
        "mqtt_tail_distance_m": round(_trail_distance(tail), 2),
        "anchor_xy": list(clean[segment_index][point_index]),
    }


def vendor_rows_revision(rows: list[dict[str, Any]]) -> str:
    parts = []
    for row in rows:
        sig = row.get("signature") or []
        parts.append(json.dumps(sig, separators=(",", ":"), sort_keys=True))
    return "|".join(parts)


def build_vendor_render_source(
    rows: list[dict[str, Any]],
    *,
    mowing_path_width_m: float | None,
) -> dict[str, Any]:
    """Build a synthetic completed session from retained vendor geometry.

    This beta intentionally treats every retained vendor edge as visible trail so
    source-lag can be debugged independently of the still-unconfirmed kr/pt flag
    semantics. The source remains separate from persistent MQTT history.
    """
    points: list[list[Any]] = []
    starts: list[int] = []
    sequence = 0
    for row_index, row in enumerate(rows):
        zone_id = _as_int(row.get("zone_id"))
        raw_points = row.get("points") or []
        if zone_id is None or len(raw_points) < 2:
            continue
        base_ms = (
            _as_int(row.get("start_time")) or (1_700_000_000 + row_index)
        ) * 1000
        starts.append(base_ms + sequence)
        for raw in raw_points:
            if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                continue
            x = _as_float(raw[0])
            y = _as_float(raw[1])
            if x is None or y is None:
                continue
            stamp = base_ms + sequence
            sequence += 1
            points.append([stamp, x, y, 0.0, "mowing", 4, 5, zone_id])
    return {
        "id": "vendor-trail-debug",
        "active": False,
        "points": points,
        "segment_starts_ms": starts,
        "mowing_path_width_m": mowing_path_width_m,
    }


class VendorTrailCurrentCycleRenderManager(CurrentCycleRenderManager):
    """Overlay vendor-retained trail on the normal current-cycle render."""

    def __init__(self, coordinator: Any) -> None:
        super().__init__(coordinator)
        self._vendor_revision: str | None = None
        self._vendor_artifact: dict[str, Any] | None = None

    async def async_get(self, map_zones: list[dict[str, Any]]) -> dict[str, Any]:
        base = await super().async_get(map_zones)
        snapshot = self.coordinator.data or {}
        cache = getattr(self.coordinator, "_vendor_trail_cache", {})
        rows = current_vendor_rows(snapshot, cache)
        revision = vendor_rows_revision(rows)
        if not revision:
            return base

        if revision != self._vendor_revision:
            width = _as_float(snapshot.get("mowing_path_width_m"))
            if width is None or not 0.1 <= width <= 2.0:
                width = SWATH_WIDTH_M
            source = build_vendor_render_source(rows, mowing_path_width_m=width)
            artifact = None
            if len(source.get("points") or []) >= 2:
                artifact = await self.coordinator.hass.async_add_executor_job(
                    build_session_svg_archive,
                    source,
                )
            self._vendor_revision = revision
            self._vendor_artifact = artifact

        artifact = self._vendor_artifact
        vendor_path = (
            str((artifact.get("mowed_area") or {}).get("path_d") or "")
            if isinstance(artifact, dict)
            else ""
        )
        if not vendor_path:
            return base

        result = deepcopy(base)
        area = dict(result.get("mowed_area") or {})
        area["path_d"] = f"{area.get('path_d') or ''}{vendor_path}"
        result["mowed_area"] = area
        result["revision"] = f"{result.get('revision') or '0'}|vendor:{revision}"
        result["render_schema_version"] = (
            artifact.get("version")
            if isinstance(artifact, dict)
            else SESSION_SVG_ARCHIVE_VERSION
        )
        result["vendor_trail_debug"] = {
            "enabled": True,
            "zone_ids": [_as_int(row.get("zone_id")) for row in rows],
            "point_count": sum(_as_int(row.get("point_count")) or 0 for row in rows),
            "revision": revision,
        }
        return result


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
