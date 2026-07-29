"""DataUpdateCoordinator for Navimower.

Polls the private cloud on a fixed interval and produces a single, defensively
parsed snapshot dict consumed by every entity. All blocking work (crypto + IO)
runs in one executor job per cycle so the event loop is never blocked.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import NavimowAuthError, NavimowCloudClient, NavimowError, Tokens
from .channel import NavimowerChannel, parse_channels
from .gate import NavimowerGate, parse_gates
from .history import (
    SESSION_CARD_POINT_FORMAT,
    SESSION_DETAIL_POINT_FORMAT,
    NavimowerHistory,
)
from .location import decode_map_work_position
from .const import (
    ACTIVE_STATES,
    ACTIVITY_DOCKED,
    ACTIVITY_ERROR,
    ACTIVITY_MOWING,
    ACTIVITY_RETURNING,
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_ID,
    CONF_LANGUAGE,
    CONF_OAUTH_TOKEN,
    CONF_PASSPORT_UUID,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    CONF_UID,
    CONF_VEHICLE_SN,
    CONF_VEHICLE_TYPE,
    DEFAULT_LANGUAGE,
    DEFAULT_SCAN_INTERVAL,
    DOCKED_STATES,
    DOMAIN,
    FAST_SCAN_INTERVAL,
    MOW_SCAN_INTERVAL,
    MQTT_CUTTING_ACTIONS,
    MQTT_DOCKED_STATES,
    MQTT_STATE_MOWING,
    MQTT_STATE_RETURNING,
    MQTT_POSE_STALE_SECONDS,
    MQTT_HISTORY_SAVE_DELAY_SECONDS,
    OPT_CHANNELS,
    OPT_GATES,
    OPT_INCLUDE_RETURN_TRAIL,
    OPT_TRAIL_RETENTION_DAYS,
    OPT_ZONES,
    DEFAULT_TRAIL_RETENTION_DAYS,
    MAP_API_SCHEMA_VERSION,
    SLOW_REFRESH_EVERY,
    STATE_MOWING,
    STATE_RETURNING,
    TUNNEL_DETECTION_RADIUS_M,
    ZONE_EDGE_TOLERANCE_M,
    VEHICLE_STATE_LABELS,
    VEHICLE_STATE_TO_ACTIVITY,
    decode_partition_id_list,
    encode_partition_ids,
)

_LOGGER = logging.getLogger(__name__)

# Slow-changing endpoints refreshed only every SLOW_REFRESH_EVERY cycles.
_SLOW_KEYS = ("set_list", "maintenance", "today_plan", "map_list")

# Persist the latest decoded map so MQTT/history remain useful during a
# temporary private-cloud outage.  Trail sessions use separate Store files.
_STATE_STORE_VERSION = 1


def state_store(hass: HomeAssistant, entry_id: str) -> Store:
    """Return the per-entry cached map/state Store."""
    key = f"{DOMAIN}_state_{entry_id}"
    try:
        return Store(hass, _STATE_STORE_VERSION, key, serialize_in_event_loop=False)
    except TypeError:
        return Store(hass, _STATE_STORE_VERSION, key)


def _find(obj: Any, *keys: str) -> Any:
    """Recursively return the first value found for any of ``keys`` (DFS)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys:
                return v
        for v in obj.values():
            found = _find(v, *keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find(v, *keys)
            if found is not None:
                return found
    return None


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


def _utc_iso_from_seconds(value: Any) -> str | None:
    """Convert a vendor Unix-seconds value to UTC ISO without raising."""
    parsed = _as_int(value)
    if parsed is None or parsed <= 0:
        return None
    try:
        return datetime.fromtimestamp(parsed, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    """Interpret the mower's many truthy encodings ('01', 1, '1', True)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).strip().lower()
    if s in ("1", "01", "true", "on", "yes"):
        return True
    if s in ("0", "00", "false", "off", "no", ""):
        return False
    return None


def _parse_zone_options(raw: str | None) -> list[dict]:
    """Parse the 'id:name,id:name' options string into zone dicts."""
    zones: list[dict] = []
    if not raw:
        return zones
    for chunk in str(raw).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            rid_s, name = chunk.split(":", 1)
        else:
            rid_s, name = chunk, f"Zone {chunk}"
        rid = _as_int(rid_s)
        if rid is None:
            continue
        zones.append({"id": rid, "name": name.strip() or f"Zone {rid}"})
    return zones


def _point_in_polygon(x: float, y: float, polygon: Any) -> bool:
    """Return whether a local X/Y point is inside or on a zone polygon."""
    if not isinstance(polygon, list) or len(polygon) < 3:
        return False
    inside = False
    count = len(polygon)
    for index in range(count):
        p1 = polygon[index]
        p2 = polygon[(index + 1) % count]
        if not (isinstance(p1, (list, tuple)) and isinstance(p2, (list, tuple))):
            continue
        if len(p1) < 2 or len(p2) < 2:
            continue
        x1, y1 = _as_float(p1[0]), _as_float(p1[1])
        x2, y2 = _as_float(p2[0]), _as_float(p2[1])
        if None in (x1, y1, x2, y2):
            continue
        # Treat points on an edge as inside.
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


def _zone_at_position(position: dict | None, zones: Any) -> dict | None:
    """Find the mapped zone containing or immediately bordering a live pose."""
    if not position or not isinstance(zones, list):
        return None
    x, y = _as_float(position.get("x")), _as_float(position.get("y"))
    if x is None or y is None:
        return None
    for zone in zones:
        if isinstance(zone, dict) and _point_in_polygon(x, y, zone.get("polygon")):
            return zone
    # Boundary mowing can place the robot centre a few centimetres outside the
    # stored polygon. Treat the nearest edge within a small mower-scale tolerance
    # as that zone so an arrival does not leave a gate open indefinitely.
    nearest: tuple[float, dict] | None = None
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        distance = _polygon_edge_distance(x, y, zone.get("polygon"))
        if distance is not None and distance <= ZONE_EDGE_TOLERANCE_M:
            if nearest is None or distance < nearest[0]:
                nearest = (distance, zone)
    return ({**nearest[1], "source": "map_polygon_edge_tolerance"} if nearest else None)


def _distance_to_segment(
    x: float, y: float, x1: float, y1: float, x2: float, y2: float
) -> float:
    """Shortest distance from a point to one local-coordinate line segment."""
    dx, dy = x2 - x1, y2 - y1
    denom = dx * dx + dy * dy
    if denom <= 1e-12:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / denom))
    px, py = x1 + t * dx, y1 + t * dy
    return ((x - px) ** 2 + (y - py) ** 2) ** 0.5


def _polygon_edge_distance(x: float, y: float, polygon: Any) -> float | None:
    """Return shortest distance from a point to a polygon perimeter."""
    if not isinstance(polygon, list) or len(polygon) < 2:
        return None
    best = float("inf")
    count = len(polygon)
    for index in range(count):
        a = polygon[index]
        b = polygon[(index + 1) % count]
        if not (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple))):
            continue
        if len(a) < 2 or len(b) < 2:
            continue
        values = (_as_float(a[0]), _as_float(a[1]), _as_float(b[0]), _as_float(b[1]))
        if any(value is None for value in values):
            continue
        best = min(best, _distance_to_segment(x, y, *values))
    return best if best != float("inf") else None


def _tunnel_at_position(
    position: dict | None, tunnels: Any, radius: float = TUNNEL_DETECTION_RADIUS_M
) -> dict | None:
    """Return the nearest mapped tunnel when the mower is close to its path."""
    if not position or not isinstance(tunnels, list):
        return None
    x, y = _as_float(position.get("x")), _as_float(position.get("y"))
    if x is None or y is None:
        return None
    nearest: tuple[float, dict] | None = None
    for tunnel in tunnels:
        if not isinstance(tunnel, dict):
            continue
        points = tunnel.get("points")
        if not isinstance(points, list) or len(points) < 2:
            continue
        best = float("inf")
        for index in range(len(points) - 1):
            a, b = points[index], points[index + 1]
            if not (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple))):
                continue
            if len(a) < 2 or len(b) < 2:
                continue
            values = (_as_float(a[0]), _as_float(a[1]), _as_float(b[0]), _as_float(b[1]))
            if any(value is None for value in values):
                continue
            best = min(best, _distance_to_segment(x, y, *values))
        if best <= radius and (nearest is None or best < nearest[0]):
            nearest = (best, tunnel)
    if nearest is None:
        return None
    return {**nearest[1], "distance": nearest[0]}


# --------------------------------------------------------------------- map
# Navimow weekday numbering is 1=Sun .. 7=Sat (verified live: day 3 = Tue, 6 = Fri).
_WEEKDAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


def _zstd_decompress(raw: bytes) -> bytes | None:
    """Decompress a ZSTD frame that carries no content-size header.

    The map blob is a raw ZSTD frame without the (optional) decompressed-size in
    its header, so a one-shot ``decompress()`` cannot pre-size its output and
    fails. Both paths below stream the output instead:

    * Python 3.14+ stdlib ``compression.zstd`` (the HA host), and
    * the ``zstandard`` PyPI package via ``stream_reader`` (fallback).

    Returns the decompressed bytes, or ``None`` if no decoder is available /
    the frame is unreadable (the integration then degrades to "no map").
    """
    # 1) Python 3.14+ standard library `compression.zstd` (no external dep).
    try:
        import compression.zstd as _cz  # type: ignore[import-not-found]
    except Exception as err:  # noqa: BLE001 - module not built into this Python.
        _LOGGER.warning("map: stdlib compression.zstd unavailable (%s)", err)
    else:
        try:
            return _cz.decompress(raw)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("map: compression.zstd.decompress failed", exc_info=True)
            try:  # headerless frame -> stream via the decompressor object.
                return _cz.ZstdDecompressor().decompress(raw)
            except Exception:  # noqa: BLE001
                _LOGGER.warning("map: compression.zstd streaming failed", exc_info=True)
    # 2) zstandard PyPI package (fallback; stream_reader handles headerless frames).
    try:
        import zstandard  # type: ignore[import-not-found]

        return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)).read()
    except Exception:  # noqa: BLE001 - no usable decoder available.
        _LOGGER.warning("map: no zstd decoder available (map geometry disabled)")
        return None


def _points_xy(points: Any) -> list[list[float]]:
    """Extract [x, y] pairs from a list of [x, y, flag, seq, ...] points."""
    out: list[list[float]] = []
    if not isinstance(points, list):
        return out
    for p in points:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            x, y = _as_float(p[0]), _as_float(p[1])
            if x is not None and y is not None:
                out.append([x, y])
    return out


def _boundary_points(points: Any) -> tuple[list[list[float]], list[int | None]]:
    """Split a BOUNDARY point list into an [x, y] polygon and per-point flags.

    Each raw point is ``[x, y, attr, seq, ...]``; ``attr`` (3rd element) is the
    boundary segment attribute that drives the perimeter line style (dashed vs
    solid). Returns ``(polygon, flags)`` with ``flags[i]`` aligned to
    ``polygon[i]`` (``None`` when a point carries no attribute).
    """
    poly: list[list[float]] = []
    flags: list[int | None] = []
    if not isinstance(points, list):
        return poly, flags
    for p in points:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            x, y = _as_float(p[0]), _as_float(p[1])
            if x is None or y is None:
                continue
            poly.append([x, y])
            flags.append(_as_int(p[2]) if len(p) >= 3 else None)
    return poly, flags


def _extract_geometry(geom: dict) -> dict:
    """Reduce raw map JSON to the versioned geometry consumed by HA/UI."""
    zones: list[dict[str, Any]] = []
    station: dict[str, Any] | None = None
    doodle_zone: dict[int, int] = {}

    for sm in geom.get("sub_maps") or []:
        if not isinstance(sm, dict):
            continue
        zid = _as_int(sm.get("id"))
        polygon: list[list[float]] = []
        boundary_flags: list[int | None] = []
        boundary_meta: dict[str, Any] = {}
        doodle_ids = [
            value
            for item in sm.get("contain_doodles_id") or []
            if (value := _as_int(item)) is not None
        ]
        if zid is not None:
            for doodle_id in doodle_ids:
                doodle_zone[doodle_id] = zid

        for el in sm.get("elements") or []:
            if not isinstance(el, dict):
                continue
            etype = el.get("type")
            if etype == "BOUNDARY" and not polygon:
                polygon, boundary_flags = _boundary_points(el.get("points"))
                boundary_meta = {
                    key: el.get(key)
                    for key in (
                        "clock_direction",
                        "boundary_type",
                        "base_angle",
                        "rec_base_angle",
                        "mow_edge",
                        "obstacle_mow_edge",
                        "doodle_mow_edge",
                        "edge_vf",
                        "height_set",
                        "avai_segs",
                        "ts_switch",
                    )
                    if key in el
                }
            elif etype == "CHARGING_PILE" and station is None:
                pos = _points_xy([el.get("position")])
                if pos:
                    station = {
                        "x": pos[0][0],
                        "y": pos[0][1],
                        "direction": _as_float(el.get("direction")),
                        "width": _as_float(el.get("width")),
                        "length": _as_float(el.get("length")),
                        "center_offset": _as_float(el.get("center_offset")),
                        "nav_pos": (_points_xy([el.get("nav_pos")]) or [None])[0],
                    }
        if zid is None and not polygon:
            continue
        zones.append(
            {
                "id": zid,
                "name": str(
                    sm.get("name")
                    or (f"Zone {zid}" if zid is not None else "Zone")
                ),
                "area": _as_float(sm.get("area")),
                "polygon": polygon,
                "boundary_flags": boundary_flags,
                "boundary": boundary_meta,
                "doodle_ids": doodle_ids,
            }
        )

    def polygons(key: str) -> list[list[list[float]]]:
        return [
            points
            for item in (geom.get(key) or [])
            if isinstance(item, dict)
            and (points := _points_xy(item.get("points")))
        ]

    tunnels: list[dict[str, Any]] = []
    for item in geom.get("tunnels") or []:
        if not isinstance(item, dict):
            continue
        points = _points_xy(item.get("points"))
        if points:
            tunnels.append(
                {
                    "id": _as_int(item.get("id")),
                    "name": str(item.get("name") or ""),
                    "points": points,
                    "connection": [
                        value
                        for raw in item.get("connection") or []
                        if (value := _as_int(raw)) is not None
                    ],
                    "tunnel_type": item.get("tunnel_type"),
                }
            )

    # Time-limited obstacles (called "doodles" by the mobile app) carry the
    # original SVG plus the transform needed to place it in local map space.
    doodles: list[dict[str, Any]] = []
    for item in geom.get("time_limit_obstacles") or []:
        if not isinstance(item, dict):
            continue
        doodle_id = _as_int(item.get("id"))
        center = _points_xy([item.get("center")])
        if doodle_id is None or not center:
            continue
        doodles.append(
            {
                "id": doodle_id,
                "zone_id": doodle_zone.get(doodle_id),
                "name": str(item.get("name") or "doodle"),
                "type": str(item.get("type") or "TIME_LIMIT_OBSTACLE"),
                "center": center[0],
                "direction": _as_float(item.get("direction")),
                "scale": _as_float(item.get("scale")),
                "create_ts": _as_int(item.get("create_ts")),
                "expiration_ts": _as_int(item.get("expiration_ts")),
                "created_at": _utc_iso_from_seconds(item.get("create_ts")),
                "expires_at": _utc_iso_from_seconds(item.get("expiration_ts")),
                "svg": str(item.get("svg") or ""),
            }
        )

    return {
        "id": _as_int(geom.get("id")),
        "name": str(geom.get("name") or "Map"),
        "area": _as_float(geom.get("area")),
        "width": _as_float(geom.get("map_width")),
        "height": _as_float(geom.get("map_height")),
        "north_offset": _as_float(geom.get("map_north_offset")),
        "version": geom.get("version"),
        "modified_count": _as_int(geom.get("modifiedCount")),
        "lidar_sha256": geom.get("lidar_sha256"),
        "zones": zones,
        "obstacles": polygons("obstacles"),
        "vision_off": polygons("vision_off_areas"),
        "tunnels": tunnels,
        "doodles": doodles,
        "terrain_sense": deepcopy_json(geom.get("terrain_sense") or []),
        "station": station,
    }


def deepcopy_json(value: Any) -> Any:
    """Return a JSON-safe deep copy without importing copy in hot parsing."""
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return None


def _parse_map_detail(blob: Any) -> dict | None:
    """base64 -> zstd -> JSON -> map_detail(JSON string) -> reduced geometry.

    Fully defensive: any failure returns ``None`` (the map degrades gracefully).
    ``blob`` is the ``data`` field of map-detail-compress (a base64 string).
    """
    if not blob or not isinstance(blob, str):
        return None
    try:
        raw = base64.b64decode(blob)
    except Exception:  # noqa: BLE001
        return None
    decompressed = _zstd_decompress(raw)
    if not decompressed:
        return None
    try:
        outer = json.loads(decompressed)
        detail = outer.get("map_detail") if isinstance(outer, dict) else None
        geom = json.loads(detail) if isinstance(detail, str) else detail
    except (ValueError, TypeError):
        return None
    if not isinstance(geom, dict):
        return None
    try:
        return _extract_geometry(geom)
    except Exception:  # noqa: BLE001 - never raise into the coordinator.
        _LOGGER.debug("map geometry extraction failed", exc_info=True)
        return None


def _parse_map_detail_plain(data: Any) -> dict | None:
    """Extract geometry from the UNCOMPRESSED /map/index/map-detail response.

    ``data`` is a DeviceMapInfo dict whose ``map_detail`` field is a JSON string
    (the same geometry that the -compress variant zstd-packs). No zstd needed.
    Fully defensive: any failure returns ``None``.
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            return None
    if not isinstance(data, dict):
        return None
    detail = data.get("map_detail")
    geom: Any = None
    if isinstance(detail, str) and detail.strip():
        try:
            geom = json.loads(detail)
        except (ValueError, TypeError):
            geom = None
    elif isinstance(detail, dict):
        geom = detail
    if not isinstance(geom, dict) or "sub_maps" not in geom:
        return None
    try:
        return _extract_geometry(geom)
    except Exception:  # noqa: BLE001 - never raise into the coordinator.
        _LOGGER.debug("plain map geometry extraction failed", exc_info=True)
        return None


def _slot_hhmm(slot: Any) -> str | None:
    """15-minute slot index from 00:00 -> 'HH:MM' (39 -> '09:45')."""
    s = _as_int(slot)
    if s is None:
        return None
    m = s * 15
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def _schedule_source(set_list: Any) -> Any:
    """The *live* weekly plan the app shows and the robot obeys.

    The current schedule lives in ``workPlanV2`` (a.k.a. ``plan_v2``). The legacy
    ``plan`` field is dead -- the app stopped maintaining it and it stays frozen
    at an old value, so it is used only as a last resort. Verified live: editing
    a day updates ``workPlanV2`` while ``plan`` does not move. The two accepted
    key spellings cover whichever the ``set-list`` response uses.
    """
    if not isinstance(set_list, dict):
        return None
    return (
        set_list.get("plan_v2")
        or set_list.get("workPlanV2")
        or set_list.get("plan")
    )


def _parse_schedule(set_list: Any, zone_names: dict) -> list[dict]:
    """Normalize the weekly mowing plan into a UI/calendar-friendly structure.

    Source: :func:`_schedule_source` (``workPlanV2``/``plan_v2``; the legacy
    ``plan`` only as a fallback). Entry: ``{day:1-7 (1=Sun), open:0/1,
    period:[{start_time,end_time,partition_ids}|[start,end]]}``; start/end are
    15-minute slots from 00:00, empty ``partition_ids`` means all zones. Fully
    defensive.
    """
    plan = _schedule_source(set_list)
    if not isinstance(plan, list):
        return []
    out: list[dict] = []
    for entry in plan:
        if not isinstance(entry, dict):
            continue
        day = _as_int(entry.get("day"))
        if day is None or not 1 <= day <= 7:
            continue
        periods: list[dict] = []
        for p in entry.get("period") or []:
            if isinstance(p, dict):
                s, e = _as_int(p.get("start_time")), _as_int(p.get("end_time"))
                raw_ids = p.get("partition_ids") or []
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                s, e, raw_ids = _as_int(p[0]), _as_int(p[1]), []
            else:
                continue
            if s is None or e is None:
                continue
            ids = [i for i in (_as_int(x) for x in raw_ids) if i is not None]
            names = [zone_names.get(i, f"Zone {i}") for i in ids] if ids else ["All zones"]
            periods.append(
                {
                    "start_min": s * 15,
                    "end_min": e * 15,
                    "start_hhmm": _slot_hhmm(s),
                    "end_hhmm": _slot_hhmm(e),
                    "zone_ids": ids,
                    "zone_names": names,
                }
            )
        out.append(
            {
                "day": day,
                "weekday": _WEEKDAYS[day - 1],
                "enabled": bool(_as_bool(entry.get("open"))),
                "periods": periods,
            }
        )
    return out


def _parse_coverage(raw_list: Any, zone_names: dict) -> dict | None:
    """Normalize per-zone coverage and retain vendor timestamps."""
    if not isinstance(raw_list, list) or not raw_list:
        return None
    zones: list[dict[str, Any]] = []
    total_area = 0.0
    total_finished = 0.0
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        zone_id = _as_int(item.get("partitionId"))
        area = _as_float(item.get("area"))
        finished = _as_float(item.get("finishedArea"))
        percentage = _as_int(item.get("partitionPercentage"))
        if area is not None:
            total_area += area
        if finished is not None:
            total_finished += finished
        zones.append(
            {
                "id": zone_id,
                "name": (
                    zone_names.get(zone_id, f"Zone {zone_id}")
                    if zone_id is not None
                    else "Zone"
                ),
                "area": area,
                "finished": finished,
                "pct": percentage,
                "start_time": _as_int(item.get("startTime")),
                "end_time": _as_int(item.get("endTime")),
                "end_time_alias": _as_int(item.get("endTimeAlias")),
            }
        )
    if not zones:
        return None
    overall = (
        round(100.0 * total_finished / total_area) if total_area > 0 else None
    )
    return {
        "overall_pct": overall,
        "total_area": round(total_area, 2),
        "finished_area": round(total_finished, 2),
        "zones": zones,
    }


def _compute_next_mow(set_list: Any, now: Any) -> str | None:
    """Next scheduled mow as a readable "Tue 04:45".

    Reads the *live* plan via :func:`_schedule_source` (``workPlanV2``, NOT the
    dead legacy ``plan`` field). Each entry is ``{day:1-7 (1=Sun), open:0/1,
    period:[{start_time,end_time,...}|[start,end]]}`` where start/end are
    15-minute slot indices from 00:00 (19 -> 04:45). Returns the soonest upcoming
    open day+start relative to ``now`` (searching a full week including today),
    or ``None`` if nothing is scheduled.
    """
    plan = _schedule_source(set_list)
    if not isinstance(plan, list):
        return None
    day_starts: dict[int, list[int]] = {}
    for entry in plan:
        if not isinstance(entry, dict) or not _as_bool(entry.get("open")):
            continue
        day = _as_int(entry.get("day"))
        if day is None:
            continue
        for period in entry.get("period") or []:
            start = None
            if isinstance(period, (list, tuple)) and period:
                start = _as_int(period[0])
            elif isinstance(period, dict):
                start = _as_int(period.get("start_time"))
            if start is not None:
                day_starts.setdefault(day, []).append(start)
    if not day_starts:
        return None
    now_day = (now.weekday() + 1) % 7 + 1  # py Mon=0..Sun=6 -> Navimow 1=Sun..7=Sat
    now_slot = (now.hour * 60 + now.minute) / 15.0
    for offset in range(0, 8):
        day = ((now_day - 1 + offset) % 7) + 1
        for start in sorted(day_starts.get(day, [])):
            if offset == 0 and start <= now_slot:
                continue
            hh, mm = divmod(start * 15, 60)
            if hh > 23:
                continue
            when = (now + timedelta(days=offset)).replace(
                hour=hh, minute=mm, second=0, microsecond=0
            )
            return when.strftime("%a %H:%M")
    return None


class NavimowCoordinator(DataUpdateCoordinator[dict]):
    """Aggregates one mower's state for all platforms."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="navimower",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.entry = entry
        data = entry.data
        self.client = NavimowCloudClient(
            device_id=data[CONF_DEVICE_ID],
            tokens=Tokens(
                access_token=data.get(CONF_ACCESS_TOKEN, ""),
                refresh_token=data.get(CONF_REFRESH_TOKEN, ""),
                uuid=data.get(CONF_PASSPORT_UUID, ""),
                region=data.get(CONF_REGION, "fra"),
            ),
            uid=data.get(CONF_UID, ""),
            region=data.get(CONF_REGION, "fra"),
            language=data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
        )
        self.sn: str = data[CONF_VEHICLE_SN]
        self.vehicle_type: int = int(data.get(CONF_VEHICLE_TYPE, 0) or 0)
        self._cycle = 0
        self._raw_cache: dict[str, Any] = {}
        self._map_geometry: dict[str, Any] | None = None
        self._map_cache_key: tuple[str, str, str] | None = None
        self._map_dirty = False
        self._state_store = state_store(hass, entry.entry_id)
        self.history = NavimowerHistory(
            hass,
            entry.entry_id,
            self.sn,
            retention_days=int(
                entry.options.get(
                    OPT_TRAIL_RETENTION_DAYS, DEFAULT_TRAIL_RETENTION_DAYS
                )
                or 0
            ),
            include_return_trail=bool(
                entry.options.get(OPT_INCLUDE_RETURN_TRAIL, True)
            ),
        )
        # Pending native mower-zone choice; empty means all zones.
        self.selected_zone_ids: list[int] = []
        # Dense official MQTT pose is merged into private-cloud state.
        self._mqtt_location: dict[str, Any] | None = None
        self._mqtt_last_update: float | None = None
        self._mqtt_connected = False
        self._mqtt_configured = bool(entry.data.get(CONF_OAUTH_TOKEN))
        self._oauth_connected = False
        self._private_cloud_connected = False
        self._last_private_error: str | None = None
        self._last_oauth_error: str | None = None
        self._last_mqtt_error: str | None = None
        self.channels: list[NavimowerChannel] = parse_channels(
            entry.options.get(OPT_CHANNELS)
        )
        self.gates: list[NavimowerGate] = parse_gates(
            entry.options.get(OPT_GATES)
        )
        self._gate_latches: dict[str, dict[str, Any]] = {}
        self._last_target_zone_ids: list[int] = []
        self._private_reauth_started = False
        self._gate_release_tasks: dict[str, Any] = {}
        self._shutdown_complete = False

    async def async_load_persistent_state(self) -> None:
        """Restore retained sessions and the last decoded map."""
        await self.history.async_load()
        try:
            cached = await self._state_store.async_load()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Map-state restore failed", exc_info=True)
            cached = None
        if isinstance(cached, dict) and cached.get("sn") in (None, self.sn):
            geometry = cached.get("map_geometry")
            if isinstance(geometry, dict):
                self._map_geometry = geometry
            key = cached.get("map_cache_key")
            geometry_schema = _as_int(cached.get("geometry_schema"))
            if (
                geometry_schema == MAP_API_SCHEMA_VERSION
                and isinstance(key, (list, tuple))
                and len(key) == 3
            ):
                self._map_cache_key = tuple(str(value) for value in key)
            else:
                # v0.1.x cached reduced geometry did not contain doodles,
                # effective heights or a frontend-visible map revision. Keep it
                # for bootstrap display but force a one-time cloud re-decode.
                self._map_cache_key = None

        # Always expose a bootstrap snapshot before the network branches start.
        # Cached geometry/history is included when present; otherwise entities can
        # still come up and independently report private/OAuth/MQTT health.
        self.async_set_updated_data(self._bootstrap_snapshot())

    def bootstrap_snapshot(self) -> dict[str, Any]:
        """Return cached/local data suitable for partial offline setup."""
        return self._bootstrap_snapshot()

    def _bootstrap_snapshot(self) -> dict[str, Any]:
        map_geometry = self._map_geometry or {}
        zones = [
            {
                "id": zone.get("id"),
                "name": zone.get("name"),
                "area": zone.get("area"),
                "partition_ids_hex": (
                    encode_partition_ids([zone_id])
                    if (zone_id := _as_int(zone.get("id"))) is not None
                    else ""
                ),
            }
            for zone in map_geometry.get("zones") or []
            if isinstance(zone, dict)
        ]
        map_payload = self._map_snapshot(map_geometry) if map_geometry else None
        snapshot: dict[str, Any] = {
            "vehicle_sn": self.sn,
            "vehicle_type": self.vehicle_type,
            "name": self.entry.title,
            "model": self.entry.data.get("model", ""),
            "state": "Unknown",
            "state_code": "",
            "activity": None,
            "online": None,
            "docked": None,
            "error": False,
            "zones": zones,
            "current_zone": None,
            "current_zone_ids": [],
            "coverage": None,
            "settings": {},
            "maintenance": {},
            "position": None,
            "map": map_payload,
            "zone_details": self._merge_zone_history(
                self._build_zone_details(None, None)
            ),
            "trail": self.history.active_points_xy(),
            "sessions": self.history.session_summaries(include_points=False),
            "trail_active": self.history.active_session is not None,
            "channels": [channel.as_dict() for channel in self.channels],
            "gates": [gate.as_dict() for gate in self.gates],
            "raw": {},
        }
        snapshot.update(self._connectivity_fields())
        snapshot.update(self._navigation_fields(snapshot))
        return snapshot

    async def async_shutdown(self) -> None:
        """Stop coordinator polling and flush history/cached geometry once."""
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        for cancel in list(self._gate_release_tasks.values()):
            try:
                cancel()
            except Exception:  # noqa: BLE001
                pass
        self._gate_release_tasks.clear()
        try:
            await self.history.async_flush()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("History checkpoint failed", exc_info=True)
        try:
            await self._state_store.async_save(self._state_store_data())
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Map-state checkpoint failed", exc_info=True)
        await super().async_shutdown()

    def _state_store_data(self) -> dict[str, Any]:
        return {
            "sn": self.sn,
            "geometry_schema": MAP_API_SCHEMA_VERSION,
            "map_geometry": self._map_geometry,
            "map_cache_key": list(self._map_cache_key)
            if self._map_cache_key
            else None,
        }

    # ------------------------------------------------------------------ poll
    async def _async_update_data(self) -> dict:
        """Refresh private-cloud data while preserving valid entity states."""
        try:
            snapshot = await self.hass.async_add_executor_job(self._fetch_blocking)
        except (NavimowAuthError, NavimowError) as err:
            self.update_interval = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
            self._private_cloud_connected = False
            self._last_private_error = str(err)
            if isinstance(err, NavimowAuthError) and not self._private_reauth_started:
                self._private_reauth_started = True
                self.entry.async_start_reauth(
                    self.hass, data={"reauth_type": "private"}
                )
            snapshot = dict(self.data or self._bootstrap_snapshot())
            snapshot.update(self._connectivity_fields())
            return snapshot

        self._private_cloud_connected = True
        self._private_reauth_started = False
        self._last_private_error = None
        self._persist_session()
        snapshot = self._apply_mqtt_snapshot(snapshot)
        self.history.update_from_snapshot(snapshot)
        snapshot["zone_details"] = self._merge_zone_history(
            snapshot.get("zone_details") or []
        )
        snapshot["trail"] = self.history.active_points_xy()
        snapshot["trail_session"] = self.history.active_session_no
        snapshot["trail_started_at"] = self.history.active_started_at()
        snapshot["sessions"] = self.history.session_summaries(include_points=False)

        if self._map_dirty:
            self._map_dirty = False
            cached = self._state_store_data()
            self._state_store.async_delay_save(
                lambda: cached, MQTT_HISTORY_SAVE_DELAY_SECONDS
            )

        code = snapshot.get("state_code")
        if code == STATE_MOWING:
            interval = MOW_SCAN_INTERVAL
        elif code in ACTIVE_STATES:
            interval = FAST_SCAN_INTERVAL
        else:
            interval = DEFAULT_SCAN_INTERVAL
        self.update_interval = timedelta(seconds=interval)
        snapshot.update(self._connectivity_fields())
        return snapshot

    def _merge_zone_history(
        self, zone_details: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        history = self.history.zone_history()
        result: list[dict[str, Any]] = []
        for detail in zone_details:
            if not isinstance(detail, dict):
                continue
            zone_id = _as_int(detail.get("id"))
            persisted = history.get(str(zone_id)) if zone_id is not None else None
            # Live geometry/settings win when present; persistent timestamps fill
            # gaps instead of being erased by a current incomplete/empty cloud row.
            merged = dict(persisted or {})
            for key, value in detail.items():
                if value is not None or key not in merged:
                    merged[key] = value
            result.append(merged)
        return result

    def _session_completed(self, snapshot: dict[str, Any]) -> bool | None:
        active = self.history.active_session
        if not active:
            return None
        selected = {
            parsed
            for value in active.get("zone_ids") or []
            if (parsed := _as_int(value)) is not None
        }
        percentages = {
            _as_int(item.get("id")): _as_int(item.get("pct"))
            for item in (snapshot.get("coverage") or {}).get("zones") or []
            if isinstance(item, dict) and _as_int(item.get("id")) is not None
        }
        relevant = (
            [percentages.get(zone_id) for zone_id in selected]
            if selected
            else list(percentages.values())
        )
        known = [value for value in relevant if value is not None]
        return all(value >= 100 for value in known) if known else None

    def _active_cutting_height(self, snapshot: dict[str, Any]) -> int | None:
        target_ids = (
            snapshot.get("target_zone_ids")
            or snapshot.get("current_zone_ids")
            or []
        )
        details = {
            _as_int(item.get("id")): item
            for item in snapshot.get("zone_details") or []
            if isinstance(item, dict) and _as_int(item.get("id")) is not None
        }
        if len(target_ids) == 1:
            detail = details.get(_as_int(target_ids[0])) or {}
            height = _as_int(detail.get("cutting_height_mm"))
            if height is not None:
                return height
        return _as_int((snapshot.get("settings") or {}).get("cut_height"))

    def _update_history(
        self,
        snapshot: dict[str, Any],
        position: dict[str, Any] | None,
        mqtt_state: int | None,
        mqtt_action: int | None,
    ) -> None:
        cutting = self._is_cutting(snapshot.get("state_code"), mqtt_state, mqtt_action)
        docked = self._is_docked_state(snapshot.get("state_code"), mqtt_state)
        returning = self._is_returning_state(
            snapshot.get("state_code"), mqtt_state
        )
        zone_ids = [
            parsed
            for value in (
                snapshot.get("target_zone_ids")
                or snapshot.get("current_zone_ids")
                or []
            )
            if (parsed := _as_int(value)) is not None
        ]
        active_height = self._active_cutting_height(snapshot)
        snapshot["active_cutting_height_mm"] = active_height
        self.history.observe(
            position=position,
            pose_time=snapshot.get("pose_time"),
            heading=(position or {}).get("heading") if position else None,
            activity=str(snapshot.get("activity") or "unknown"),
            cutting=cutting,
            docked=docked,
            returning=returning,
            zone_ids=zone_ids,
            cutting_height_mm=active_height,
            mode=str(snapshot.get("mow_mode") or "mowing"),
            mqtt_vehicle_state=mqtt_state,
            mqtt_action=mqtt_action,
            completed=self._session_completed(snapshot) if docked else None,
        )

    def _fetch_blocking(self) -> dict:
        """Runs in an executor thread. Fetches + parses one snapshot."""
        sn, vtype = self.sn, self.vehicle_type
        raw = self._raw_cache

        # One-time static info (model, area limits).
        if "device_info" not in raw:
            try:
                raw["device_info"] = self.client.device_info(sn)
            except NavimowAuthError:
                raise
            except NavimowError:
                raw["device_info"] = {}

        # Fast, every cycle.
        raw["index2"] = self.client.index2(sn)
        raw["auth_list"] = self.client.auth_list()
        try:
            raw["location"] = self.client.location(sn, vtype)
        except NavimowAuthError:
            raise  # unrecoverable auth failure -> reauth (never swallow)
        except NavimowError:
            raw.setdefault("location", {})
        # Per-zone coverage (cheap; persists for the last session even when docked).
        try:
            raw["path_info_time"] = self.client.path_info_time(sn)
        except NavimowAuthError:
            raise
        except NavimowError:
            raw.setdefault("path_info_time", [])

        # Slow, only every N cycles (or on the first successful fetch).
        self._cycle = (self._cycle + 1) % SLOW_REFRESH_EVERY
        if self._cycle == 1 or "set_list" not in raw:
            getters = {
                "set_list": lambda: self.client.set_list(sn),
                "maintenance": lambda: self.client.maintenance(sn),
                "today_plan": lambda: self.client.today_plan(sn, vtype),
                "map_list": lambda: self.client.map_list(sn),
            }
            for key, getter in getters.items():
                try:
                    raw[key] = getter()
                except NavimowAuthError:
                    raise
                except NavimowError:
                    raw.setdefault(key, {})
            # Map geometry: fetch + decode on the slow cycle, cached by map
            # id/edit-time so the 2 KB blob is only downloaded when it changes.
            self._maybe_fetch_map(raw)
        return self._parse(raw)

    def _maybe_fetch_map(self, raw: dict) -> None:
        """Fetch + decode the map once, then only when the map version changes."""
        location = raw.get("location") or {}
        map_id = location.get("map_id")
        map_base_id = location.get("map_base_id")
        edit_time = location.get("map_edit_time")
        # Fall back to map-list if get-location didn't carry the ids.
        if map_id is None:
            map_list = raw.get("map_list")
            first = map_list[0] if isinstance(map_list, list) and map_list else {}
            if isinstance(first, dict):
                map_id = first.get("map_id")
                map_base_id = first.get("map_base_id")
                edit_time = first.get("edittime")
        if map_id is None or map_base_id is None:
            return

        key = (str(map_id), str(map_base_id), str(edit_time))
        if self._map_geometry is not None and self._map_cache_key == key:
            return  # cached geometry is still current.

        # Prefer the UNCOMPRESSED map-detail (plain JSON, no zstd dependency);
        # fall back to the zstd-compressed variant only if plain fails/empty.
        geometry: dict | None = None
        try:
            plain = self.client.map_detail_plain(self.sn, str(map_id), str(map_base_id))
            geometry = _parse_map_detail_plain(plain)
        except NavimowError:
            geometry = None
        if geometry is None:
            try:
                blob = self.client.map_detail(self.sn, str(map_id), str(map_base_id))
                geometry = _parse_map_detail(blob)
            except NavimowError:
                return
        if geometry is None:
            return

        geometry["map_id"] = str(map_id)
        geometry["map_base_id"] = str(map_base_id)
        geometry["edit_time"] = str(edit_time or "")
        geometry["revision"] = "|".join(key)

        # Optional dock/approach path (a different, docking-local frame -> we do
        # NOT render it, but keep it available for debugging / future overlays).
        try:
            station_raw = self.client.station_map(self.sn, str(map_id), str(map_base_id))
            pts = _points_xy((station_raw or {}).get("points"))
            if pts:
                geometry["station_map"] = {
                    "points": pts,
                    "start_from_pile": bool((station_raw or {}).get("start_from_pile")),
                }
        except NavimowError:
            pass

        self._map_geometry = geometry
        self._map_cache_key = key
        self._map_dirty = True

    # ------------------------------------------------------------- persist
    def _persist_session(self) -> None:
        state = self.client.session_state()
        merged = {
            **self.entry.data,
            CONF_ACCESS_TOKEN: state["access_token"],
            CONF_REFRESH_TOKEN: state["refresh_token"],
            CONF_PASSPORT_UUID: state["uuid"],
            CONF_UID: state["uid"],
            CONF_REGION: state["region"],
        }
        if merged != dict(self.entry.data):
            self.hass.config_entries.async_update_entry(self.entry, data=merged)

    # --------------------------------------------------------------- parsing
    def _auth_item(self, auth_list: Any) -> dict:
        if isinstance(auth_list, list):
            for it in auth_list:
                if isinstance(it, dict) and str(it.get("vehicle_sn")) == self.sn:
                    return it
            # Never fall back to another mower on multi-device accounts. A
            # shared account may legitimately return an empty list; identity is
            # already stored in the config entry in that case.
        return {}

    def _resolve_zones(self, current_ids: list[int]) -> list[dict]:
        """Available-zone list. Priority: real map zones > Options > current.

        The primary source is the decoded map geometry (``sub_maps``), which
        gives the real partition ids and names (e.g. id 1 "Zone 1", id 5
        "Zone 2"). The Options-flow ``id:name`` mapping is only a last-resort
        fallback for when the map cannot be decoded.
        """
        zones: list[dict] = []
        # 1) Real zones from the decoded map (sub_maps).
        for z in (self._map_geometry or {}).get("zones") or []:
            zid = z.get("id")
            if zid is None:
                continue
            zones.append(
                {
                    "id": zid,
                    "name": z.get("name") or f"Zone {zid}",
                    "area": z.get("area"),
                }
            )
        # 2) Fallback: user-configured mapping via the Options flow.
        if not zones:
            zones = _parse_zone_options(self.entry.options.get(OPT_ZONES))
        # 3) Last resort: whatever region is currently selected/mowing.
        if not zones and current_ids:
            zones = [{"id": rid, "name": f"Zone {rid}"} for rid in current_ids]

        # Attach the encoded command payloads.
        for z in zones:
            z["partition_ids_hex"] = encode_partition_ids([z["id"]])
        return zones

    @staticmethod
    def _map_snapshot(map_geometry: dict[str, Any]) -> dict[str, Any]:
        """Return the stable map object exposed to entities and HTTP API."""
        return {
            "id": map_geometry.get("id"),
            "map_id": map_geometry.get("map_id"),
            "map_base_id": map_geometry.get("map_base_id"),
            "edit_time": map_geometry.get("edit_time"),
            "revision": map_geometry.get("revision"),
            "name": map_geometry.get("name"),
            "area": map_geometry.get("area"),
            "zones": map_geometry.get("zones") or [],
            "obstacles": map_geometry.get("obstacles") or [],
            "vision_off": map_geometry.get("vision_off") or [],
            "tunnels": map_geometry.get("tunnels") or [],
            "doodles": map_geometry.get("doodles") or [],
            "terrain_sense": map_geometry.get("terrain_sense") or [],
            "station": map_geometry.get("station"),
            "station_map": map_geometry.get("station_map"),
            "width": map_geometry.get("width"),
            "height": map_geometry.get("height"),
            "north_offset": map_geometry.get("north_offset"),
            "version": map_geometry.get("version"),
            "modified_count": map_geometry.get("modified_count"),
            "lidar_sha256": map_geometry.get("lidar_sha256"),
        }

    def _build_zone_details(
        self, coverage: dict[str, Any] | None, global_height: int | None
    ) -> list[dict[str, Any]]:
        """Return effective height, edge settings and current zone history."""
        coverage_by_id = {
            _as_int(item.get("id")): item
            for item in (coverage or {}).get("zones") or []
            if isinstance(item, dict) and _as_int(item.get("id")) is not None
        }
        details: list[dict[str, Any]] = []
        for zone in (self._map_geometry or {}).get("zones") or []:
            if not isinstance(zone, dict):
                continue
            zone_id = _as_int(zone.get("id"))
            if zone_id is None:
                continue
            boundary = zone.get("boundary") or {}
            raw_height = _as_int(boundary.get("height_set"))
            inherits = raw_height in (None, 0, 256)
            effective_height = global_height if inherits else raw_height
            row = coverage_by_id.get(zone_id) or {}
            start_time = _as_int(row.get("start_time"))
            end_time = _as_int(row.get("end_time"))
            percentage = _as_int(row.get("pct"))
            details.append(
                {
                    "id": zone_id,
                    "name": zone.get("name") or f"Zone {zone_id}",
                    "area_m2": _as_float(zone.get("area")),
                    "percentage": percentage,
                    "finished_area_m2": _as_float(row.get("finished")),
                    "last_started_at": (
                        dt_util.utc_from_timestamp(start_time).isoformat()
                        if start_time
                        else None
                    ),
                    "last_mowed_at": (
                        dt_util.utc_from_timestamp(end_time or start_time).isoformat()
                        if (end_time or start_time)
                        else None
                    ),
                    "last_completed_at": (
                        dt_util.utc_from_timestamp(end_time).isoformat()
                        if end_time and (percentage or 0) >= 100
                        else None
                    ),
                    "configured_height_raw": raw_height,
                    "configured_height_mm": None if inherits else raw_height,
                    "cutting_height_mm": effective_height,
                    "inherits_global_height": inherits,
                    "mow_edge": _as_bool(boundary.get("mow_edge")),
                    "obstacle_mow_edge": _as_bool(
                        boundary.get("obstacle_mow_edge")
                    ),
                    "doodle_mow_edge": _as_bool(
                        boundary.get("doodle_mow_edge")
                    ),
                    "clock_direction": _as_int(
                        boundary.get("clock_direction")
                    ),
                    "base_angle": _as_int(boundary.get("base_angle")),
                    "recommended_base_angle": _as_int(
                        boundary.get("rec_base_angle")
                    ),
                }
            )
        return details

    def _parse(self, raw: dict) -> dict:
        index2 = raw.get("index2") or {}
        auth = self._auth_item(raw.get("auth_list"))
        location = raw.get("location") or {}
        set_list = raw.get("set_list") or {}
        maintenance = raw.get("maintenance") or {}
        today_plan = raw.get("today_plan") or {}

        state_code = str(index2.get("vehicle_state") or auth.get("vehicle_state") or "")
        packed_work = decode_map_work_position(
            _find(location, "map_work_position", "mapWorkPosition")
            or _find(index2, "map_work_position", "mapWorkPosition")
        )
        battery = _as_int(
            index2.get("soc") if index2.get("soc") is not None else auth.get("soc")
        )
        network_status = _as_int(index2.get("network_status"))
        online = network_status == 1 if network_status is not None else None

        # Error detection from index2's inline error array (the hint-error
        # endpoint returns a compressed blob we intentionally do not decode).
        error_list = index2.get("error_data") or _find(index2, "errorData", "error_list") or []
        has_error = bool(error_list)
        error_text = None
        if has_error and isinstance(error_list, list) and error_list:
            first = error_list[0]
            if isinstance(first, dict):
                error_text = str(
                    first.get("desc") or first.get("message") or first.get("code") or "error"
                )

        # Current zone(s) from index2.partitionIdList (big-endian). An EMPTY
        # list means "all zones / whole map" -> show "All", never "Unknown".
        current_ids = decode_partition_id_list(str(index2.get("partitionIdList") or ""))
        zones = self._resolve_zones(current_ids)
        zone_names = {z["id"]: z["name"] for z in zones}
        current_zone = (
            ", ".join(zone_names.get(i, f"Zone {i}") for i in current_ids)
            if current_ids
            else "All"
        )
        map_geom = self._map_geometry or {}

        # Coverage and position. MQTT is authoritative while fresh; private
        # get-location remains a fallback when MQTT is absent/stale.
        cloud_position = self._parse_position(location)
        coverage = _parse_coverage(raw.get("path_info_time"), zone_names)
        mqtt_position = self._fresh_mqtt_position()
        position = mqtt_position or cloud_position
        mqtt_vehicle_state = (
            _as_int((self._mqtt_location or {}).get("vehicle_state"))
            if mqtt_position is not None
            else None
        )
        mqtt_action = (
            _as_int((self._mqtt_location or {}).get("action"))
            if mqtt_position is not None
            else None
        )
        trail = self.history.active_points_xy()

        previous_activity = (self.data or {}).get("activity")
        activity = VEHICLE_STATE_TO_ACTIVITY.get(state_code)
        if self._is_cutting(state_code, mqtt_vehicle_state, mqtt_action):
            activity = ACTIVITY_MOWING
        elif mqtt_vehicle_state == MQTT_STATE_RETURNING:
            activity = ACTIVITY_RETURNING
        elif mqtt_vehicle_state in MQTT_DOCKED_STATES:
            activity = ACTIVITY_DOCKED
        elif activity is None:
            # Preserve the last trustworthy activity for short-lived unknown
            # cloud state codes instead of falsely reporting the mower docked.
            activity = previous_activity or ACTIVITY_DOCKED
        if has_error:
            activity = ACTIVITY_ERROR

        # --- settings (MowerSettingBean; snake_case in set-list, camelCase in bean)
        night_mow = _as_bool(_find(set_list, "night_mow_switch", "nightMowSwitch"))
        rain_sensor = _as_bool(_find(set_list, "rainSensor", "rain_sensor"))
        rain_detection = _as_bool(_find(set_list, "rainDetectionSwitch", "rain_detection_switch"))
        sound = _as_bool(_find(set_list, "soundSwitch", "sound_switch"))
        power_saving = _as_bool(_find(set_list, "lowPowerSet", "low_power_set"))
        child_lock = _as_bool(_find(set_list, "childLock", "child_lock"))
        # "modern" MowerSettingBean toggles (write via save-set-data + iot_set;
        # feature-detected downstream -- entity created only when the key is
        # actually reported, so other models only see what they have).
        lift_alarm = _as_bool(_find(set_list, "liftSwitch", "lift_switch"))
        mowing_cycle = _as_bool(_find(set_list, "mowingCycle", "mowing_cycle"))
        frost_delay = _as_bool(_find(set_list, "frostSwitch", "frost_switch"))
        snow_delay = _as_bool(_find(set_list, "snowSwitch", "snow_switch"))
        storm_delay = _as_bool(_find(set_list, "stormSwitch", "storm_switch"))
        high_temp_delay = _as_bool(_find(set_list, "highTempSwitch", "high_temp_switch"))
        # vision / advanced (captured live): slamSwitch=EFLS (camera positioning),
        # cptSwitch=obstacle avoidance, tractionControl=traction. animalProtection
        # and lightSwitch are write-only (not reported) -> no read here.
        efls = _as_bool(_find(set_list, "slamSwitch", "slam_switch"))
        obstacle_avoid = _as_bool(_find(set_list, "cptSwitch", "cpt_switch"))
        traction = _as_bool(_find(set_list, "tractionControl", "traction_control"))
        night_light_level = _as_int(_find(set_list, "nightLightLevel", "night_light_level"))
        # rain / weather-forecast zone (captured live, distinct from the physical
        # rainSensor/rainDetectionSwitch above): weatherSwitch=master on/off,
        # weatherSensitivity=drizzle 0/light 1/moderate 2, delayedPileSwitch=
        # continue(0)/delay(1), delayedPileSet=delay time (wire = hours*4).
        weather_switch = _as_bool(_find(set_list, "weatherSwitch", "weather_switch"))
        weather_sensitivity = _as_int(_find(set_list, "weatherSensitivity", "weather_sensitivity"))
        rain_behavior = _as_bool(_find(set_list, "delayedPileSwitch", "delayed_pile_switch"))
        # delayedPileSet: try decimal (set-list style) then hex; store the raw wire
        # value (number.py divides by the per-entity scale to show hours).
        _rd = _find(set_list, "delayedPileSet", "delayed_pile_set")
        rain_delay_wire: int | None = None
        if _rd is not None:
            try:
                rain_delay_wire = int(str(_rd).strip(), 10)
            except (TypeError, ValueError):
                try:
                    rain_delay_wire = int(str(_rd).strip(), 16)
                except (TypeError, ValueError):
                    rain_delay_wire = None

        settings = {
            "night_mow": night_mow,
            "rain_sensor": rain_sensor,
            "rain_detection": rain_detection,
            "sound": sound,
            "power_saving": power_saving,
            "child_lock": child_lock,
            "lift_alarm": lift_alarm,
            "mowing_cycle": mowing_cycle,
            "frost_delay": frost_delay,
            "snow_delay": snow_delay,
            "storm_delay": storm_delay,
            "high_temp_delay": high_temp_delay,
            "efls": efls,
            "obstacle_avoid": obstacle_avoid,
            "traction": traction,
            "night_light_level": night_light_level,
            "cut_height": _as_int(_find(set_list, "height")),
            # set-list reports these percentages as DECIMAL (10 / 100). Only the
            # app's internal bean and the device (s:mower) write use hex -- the
            # number entities write hex to the robot, decimal to the cloud.
            "return_battery_level": _as_int(_find(set_list, "returnBatteryLevel")),
            "charging_limit": _as_int(_find(set_list, "chargingLimit")),
            # rain / weather-forecast zone
            "weather_switch": weather_switch,
            "weather_sensitivity": weather_sensitivity,
            "rain_behavior": rain_behavior,
            "rain_delay_wire": rain_delay_wire,
        }

        zone_details = self._build_zone_details(coverage, settings["cut_height"])

        # --- maintenance (blades / chassis) -- field names are firmware-specific
        maint = self._parse_maintenance(maintenance)

        snapshot: dict[str, Any] = {
            # identity / static
            "vehicle_sn": self.sn,
            "vehicle_type": self.vehicle_type,
            "model": str(auth.get("subType") or _find(raw.get("device_info"), "model") or ""),
            "name": str(auth.get("selfDefinedName") or auth.get("vehicle_name") or "Navimow"),
            # core state
            "battery": battery,
            "state_code": state_code,
            "state": VEHICLE_STATE_LABELS.get(
                state_code,
                f"Unknown ({state_code})" if state_code else "Unknown",
            ),
            "activity": activity,
            "mqtt_vehicle_state": mqtt_vehicle_state,
            "mqtt_action": mqtt_action,
            "trail_active": self._is_cutting(state_code, mqtt_vehicle_state, mqtt_action),
            "online": online,
            "docked": self._is_docked_state(state_code, mqtt_vehicle_state),
            "error": has_error,
            "error_text": error_text,
            # progress / areas
            "mowing_progress": _as_int(
                _find(
                    location,
                    "mowing_percentage",
                    "mowingPercentage",
                    "progress",
                )
            ),
            "session_area": _as_float(location.get("subtotal_area")),
            "weekly_area": _as_float(location.get("mowing_week_area")),
            "total_area": (
                map_geom.get("area")
                if map_geom.get("area") is not None
                else _as_float(_find(raw.get("device_info"), "map_area_limit"))
            ),
            "next_mow": _compute_next_mow(set_list, dt_util.now()),
            # zones
            "zones": zones,
            "current_zone": current_zone,
            "current_zone_ids": current_ids,
            "work_target_zone": _as_int((packed_work or {}).get("target_zone")),
            "work_action": _as_int((packed_work or {}).get("action")),
            "work_sub_action": _as_int((packed_work or {}).get("sub_action")),
            "work_mode": _as_int((packed_work or {}).get("mode")),
            "work_progress": _as_int((packed_work or {}).get("progress")),
            # weekly mowing schedule (days -> periods -> zones)
            "schedule": _parse_schedule(set_list, zone_names),
            # connectivity
            "signal": _as_int(index2.get("network_signal") or auth.get("network_signal")),
            "signal_wifi": _as_int(
                index2.get("network_signal_wifi")
                or auth.get("network_signal_wifi")
            ),
            "signal_4g": _as_int(index2.get("network_signal_4G") or auth.get("network_signal_4G")),
            "network_type": _as_int(index2.get("networkType") or auth.get("networkType")),
            # location / map
            "latitude": _as_float(_find(location, "latitude", "lat")),
            "longitude": _as_float(_find(location, "longitude", "lng", "lon")),
            "position": position,
            "cloud_position": cloud_position,
            "pose_source": "mqtt" if mqtt_position is not None else "private_cloud",
            "pose_time": (
                (self._mqtt_location or {}).get("pose_time")
                if mqtt_position is not None
                else _find(location, "report_time")
            ),
            "path": self._parse_path(location),
            # per-zone coverage (%) + reconstructed mowed trail ([[x,y],...])
            "coverage": coverage,
            "zone_details": zone_details,
            "cut_height": settings.get("cut_height"),
            "cutting_height_mm": settings.get("cut_height"),
            "trail": trail,
            # decoded map geometry (None until the map is fetched/decoded)
            "map": self._map_snapshot(map_geom) if map_geom else None,
            # groups
            "settings": settings,
            "maintenance": maint,
            # source health / local channels
            **self._connectivity_fields(),
            "channels": [channel.as_dict() for channel in self.channels],
            "gates": [gate.as_dict() for gate in self.gates],
            # raw (for entity extra attributes / debugging)
            "raw": {
                "index2": index2,
                "auth_item": auth,
                "location": location,
                "set_list": set_list,
                "maintenance": maintenance,
                "today_plan": today_plan,
                "path_info_time": raw.get("path_info_time") or [],
                "device_info": raw.get("device_info") or {},
            },
        }
        return snapshot

    def _fresh_mqtt_position(self) -> dict[str, float] | None:
        if self._mqtt_location is None or self._mqtt_last_update is None:
            return None
        if time.monotonic() - self._mqtt_last_update > MQTT_POSE_STALE_SECONDS:
            return None
        x, y = _as_float(self._mqtt_location.get("x")), _as_float(self._mqtt_location.get("y"))
        if x is None or y is None:
            return None
        return {"x": x, "y": y, "heading": _as_float(self._mqtt_location.get("theta"))}

    @staticmethod
    def _is_cutting(
        state_code: str | None,
        mqtt_vehicle_state: int | None,
        mqtt_action: int | None = None,
    ) -> bool:
        """Return whether the freshest available source says the blade is mowing.

        ``action`` persists in the MQTT location cache between messages.  A
        fresh non-mowing ``vehicleState`` must therefore override an earlier
        action=5/8 value, otherwise a docked mower could keep an active session
        open indefinitely.
        """
        if mqtt_vehicle_state is not None:
            return mqtt_vehicle_state == MQTT_STATE_MOWING
        if mqtt_action in MQTT_CUTTING_ACTIONS:
            return True
        return str(state_code or "") == STATE_MOWING

    @staticmethod
    def _is_docked_state(
        state_code: str | None,
        mqtt_vehicle_state: int | None,
    ) -> bool:
        """Return docked state, preferring a fresh MQTT vehicle state."""
        if mqtt_vehicle_state is not None:
            return mqtt_vehicle_state in MQTT_DOCKED_STATES
        return str(state_code or "") in DOCKED_STATES

    @staticmethod
    def _is_returning_state(
        state_code: str | None,
        mqtt_vehicle_state: int | None,
    ) -> bool:
        """Return returning state, preferring a fresh MQTT vehicle state."""
        if mqtt_vehicle_state is not None:
            return mqtt_vehicle_state == MQTT_STATE_RETURNING
        return str(state_code or "") == STATE_RETURNING

    def _cancel_gate_release(self, slug: str) -> None:
        cancel = self._gate_release_tasks.pop(slug, None)
        if cancel is not None:
            try:
                cancel()
            except Exception:  # noqa: BLE001
                pass

    def _schedule_gate_release(self, slug: str, delay: int) -> None:
        """Publish the delayed OFF state even if no further pose arrives."""
        self._cancel_gate_release(slug)
        if delay <= 0:
            return

        def _release(_now: Any) -> None:
            self._gate_release_tasks.pop(slug, None)
            latch = self._gate_latches.get(slug)
            release_at = _as_float((latch or {}).get("release_at"))
            if latch is None or release_at is None or time.monotonic() < release_at:
                return
            self._gate_latches.pop(slug, None)
            if self.data:
                updated = dict(self.data)
                updated.update(self._navigation_fields(updated))
                self.async_set_updated_data(updated)

        self._gate_release_tasks[slug] = async_call_later(
            self.hass, delay, _release
        )

    def _navigation_fields(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Derive physical zone, target zone, tunnel and gate intent from live X/Y."""
        position = self._fresh_mqtt_position()
        pose_valid = position is not None
        map_data = snapshot.get("map") or {}
        zones = map_data.get("zones") or snapshot.get("zones") or []
        tunnels = map_data.get("tunnels") or []
        zone_names: dict[int, str] = {}
        for zone in zones:
            if not isinstance(zone, dict):
                continue
            zone_id = _as_int(zone.get("id"))
            if zone_id is not None:
                zone_names[zone_id] = str(zone.get("name") or f"Zone {zone_id}")

        physical = _zone_at_position(position, zones) if pose_valid else None
        # When decoded geometry is unavailable, currentMowBoundary is the best
        # live fallback. Do not use it while geometry exists because it can stay
        # on the previous zone while the mower is physically in a tunnel.
        if physical is None and pose_valid and not zones:
            fallback_id = _as_int((self._mqtt_location or {}).get("mow_boundary"))
            if fallback_id is not None:
                physical = {
                    "id": fallback_id,
                    "name": zone_names.get(fallback_id, f"Zone {fallback_id}"),
                    "source": "mqtt_current_mow_boundary",
                }

        tunnel = None
        if pose_valid and physical is None:
            tunnel = _tunnel_at_position(position, tunnels)

        station = map_data.get("station") or {}
        dock_zone = _zone_at_position(
            {"x": station.get("x"), "y": station.get("y")}, zones
        ) if station else None
        dock_zone_id = _as_int((dock_zone or {}).get("id"))

        state_code = str(snapshot.get("state_code") or "")
        mqtt_state = (
            _as_int((self._mqtt_location or {}).get("vehicle_state"))
            if pose_valid
            else None
        )
        is_docked = self._is_docked_state(state_code, mqtt_state)
        is_returning = self._is_returning_state(state_code, mqtt_state)

        target_ids: list[int] = []
        if is_docked:
            self._last_target_zone_ids = []
            self._gate_latches.clear()
            for slug in list(self._gate_release_tasks):
                self._cancel_gate_release(slug)
        else:
            immediate_target = _as_int(
                (self._mqtt_location or {}).get("work_target_zone")
            )
            if immediate_target is None:
                immediate_target = _as_int(snapshot.get("work_target_zone"))
            mqtt_ids = (self._mqtt_location or {}).get("partition_ids")
            if isinstance(mqtt_ids, list):
                target_ids = [
                    value
                    for item in mqtt_ids
                    if (value := _as_int(item)) is not None
                ]
            cloud_ids = [
                value
                for item in (snapshot.get("current_zone_ids") or [])
                if (value := _as_int(item)) is not None
            ]
            if is_returning and dock_zone_id is not None:
                target_ids = [dock_zone_id]
            elif immediate_target is not None and immediate_target > 0:
                # ``partitionIds`` can contain all zones selected for a task.
                # The packed work-position target is the immediate destination
                # and therefore the useful gate pre-open signal.
                target_ids = [immediate_target]
            elif not target_ids:
                target_ids = cloud_ids or list(self._last_target_zone_ids)
            if target_ids:
                self._last_target_zone_ids = list(dict.fromkeys(target_ids))
                target_ids = list(self._last_target_zone_ids)

        target_names = [zone_names.get(zone_id, f"Zone {zone_id}") for zone_id in target_ids]
        physical_id = _as_int((physical or {}).get("id"))
        physical_name = (physical or {}).get("name")
        tunnel_connection = [
            value
            for item in ((tunnel or {}).get("connection") or [])
            if (value := _as_int(item)) is not None
        ]

        if not pose_valid:
            physical_state = None
        elif physical_name:
            physical_state = str(physical_name)
        elif tunnel is not None:
            physical_state = "Between zones"
        else:
            physical_state = "Outside mapped zones"

        target_state = ", ".join(target_names) if target_names else None
        tunnel_state = None
        if tunnel is not None:
            tunnel_state = str(
                tunnel.get("name")
                or (f"Tunnel {tunnel.get('id')}" if tunnel.get("id") is not None else "Tunnel")
            )

        transition: bool | None
        if not pose_valid:
            transition = None
        elif len(target_ids) == 1:
            target_id = target_ids[0]
            transition = (
                (physical_id is not None and physical_id != target_id)
                or (tunnel is not None and target_id in tunnel_connection)
            )
        else:
            transition = False

        gate_states: dict[str, dict[str, Any]] = {}
        now_monotonic = time.monotonic()
        for gate in self.gates:
            pair = set(gate.zones)
            target_id = target_ids[0] if len(target_ids) == 1 else None
            intent = gate.allows_transition(physical_id, target_id)
            tunnel_from_id = gate.other_zone(target_id) if target_id is not None else None
            tunnel_direction_allowed = (
                target_id is not None
                and tunnel_from_id is not None
                and gate.allows_transition(tunnel_from_id, target_id)
            )
            tunnel_matches = (
                tunnel is not None
                and set(tunnel_connection) == pair
                and (
                    gate.slug in self._gate_latches
                    or tunnel_direction_allowed
                )
            )

            if intent:
                self._cancel_gate_release(gate.slug)
                self._gate_latches[gate.slug] = {
                    "from_zone_id": int(physical_id),
                    "to_zone_id": int(target_id),
                    "release_at": None,
                }

            latch = self._gate_latches.get(gate.slug)
            if latch:
                from_id = _as_int(latch.get("from_zone_id"))
                to_id = _as_int(latch.get("to_zone_id"))
                arrived = (
                    pose_valid
                    and tunnel is None
                    and to_id is not None
                    and physical_id == to_id
                )
                if arrived:
                    release_at = _as_float(latch.get("release_at"))
                    if release_at is None:
                        release_at = now_monotonic + gate.close_delay
                        latch["release_at"] = release_at
                        self._schedule_gate_release(gate.slug, gate.close_delay)
                    if now_monotonic >= release_at:
                        self._gate_latches.pop(gate.slug, None)
                        self._cancel_gate_release(gate.slug)
                        latch = None
                elif physical_id is not None and physical_id not in pair:
                    self._gate_latches.pop(gate.slug, None)
                    self._cancel_gate_release(gate.slug)
                    latch = None
                elif (
                    target_id is not None
                    and target_id not in pair
                    and not tunnel_matches
                ):
                    self._gate_latches.pop(gate.slug, None)
                    self._cancel_gate_release(gate.slug)
                    latch = None
                elif from_id is not None and to_id is not None:
                    # Cancel one-way latches if the reported transition reverses.
                    if not gate.allows_transition(from_id, to_id):
                        self._gate_latches.pop(gate.slug, None)
                        self._cancel_gate_release(gate.slug)
                        latch = None

            if not pose_valid:
                # Never emit a false close signal just because the MQTT pose is
                # stale while a gate transition is already latched.
                required = True if latch else None
            else:
                required = bool(intent or tunnel_matches or latch)

            from_id = _as_int((latch or {}).get("from_zone_id"))
            to_id = _as_int((latch or {}).get("to_zone_id"))
            release_at = _as_float((latch or {}).get("release_at"))
            close_delay_remaining = (
                max(0, round(release_at - now_monotonic, 1))
                if release_at is not None
                else None
            )
            gate_states[gate.slug] = {
                "required": required,
                "name": gate.name,
                "zones": list(gate.zones),
                "zone_names": [
                    zone_names.get(zone_id, f"Zone {zone_id}")
                    for zone_id in gate.zones
                ],
                "bidirectional": gate.bidirectional,
                "close_delay": gate.close_delay,
                "close_delay_remaining": close_delay_remaining,
                "from_zone_id": from_id,
                "from_zone_name": (
                    zone_names.get(from_id, f"Zone {from_id}")
                    if from_id is not None
                    else None
                ),
                "to_zone_id": to_id,
                "to_zone_name": (
                    zone_names.get(to_id, f"Zone {to_id}")
                    if to_id is not None
                    else None
                ),
                "current_zone_id": physical_id,
                "target_zone_id": target_id,
                "current_tunnel_id": _as_int((tunnel or {}).get("id")),
                "current_tunnel_name": tunnel_state,
                "pose_age": self.pose_age(),
            }

        if transition is False and any(
            state.get("required") is True for state in gate_states.values()
        ):
            transition = True

        return {
            "current_physical_zone": physical_state,
            "current_physical_zone_id": physical_id,
            "current_physical_zone_source": (
                (physical or {}).get("source", "map_polygon")
                if physical
                else None
            ),
            "target_zone": target_state,
            "target_zone_ids": target_ids,
            "current_tunnel": tunnel_state,
            "current_tunnel_id": _as_int((tunnel or {}).get("id")),
            "current_tunnel_connection": tunnel_connection,
            "current_tunnel_distance": (tunnel or {}).get("distance"),
            "dock_zone_id": dock_zone_id,
            "zone_transition": transition,
            "gate_states": gate_states,
        }

    def gate_state(self, gate: NavimowerGate) -> bool | None:
        """Return whether a configured zone-pair gate is currently required."""
        state = ((self.data or {}).get("gate_states") or {}).get(gate.slug) or {}
        value = state.get("required")
        return value if isinstance(value, bool) else None

    def gate_attributes(self, gate: NavimowerGate) -> dict[str, Any]:
        """Return current navigation context for a configured gate entity."""
        state = ((self.data or {}).get("gate_states") or {}).get(gate.slug) or {}
        return {**gate.as_dict(), **state}

    @property
    def trail_session(self) -> int:
        """Monotonic identifier of the current/recent trail session."""
        return self.history.active_session_no

    def pose_age(self) -> float | None:
        """Age in seconds of the latest MQTT pose."""
        if self._mqtt_last_update is None:
            return None
        return max(0.0, time.monotonic() - self._mqtt_last_update)

    def _connectivity_fields(self) -> dict[str, Any]:
        return {
            "private_cloud_connected": self._private_cloud_connected,
            "private_cloud_error": self._last_private_error,
            "oauth_configured": bool(self.entry.data.get(CONF_OAUTH_TOKEN)),
            "oauth_connected": self._oauth_connected,
            "oauth_error": self._last_oauth_error,
            "mqtt_configured": self._mqtt_configured,
            "mqtt_connected": self._mqtt_connected,
            "mqtt_error": self._last_mqtt_error,
            "mqtt_pose_age": self.pose_age(),
            "mqtt_pose_valid": self._fresh_mqtt_position() is not None,
        }

    def set_private_cloud_error(self, error: str | None) -> None:
        """Publish a private-cloud startup error while keeping cached data."""
        self._private_cloud_connected = False
        self._last_private_error = error
        if self.data:
            snapshot = dict(self.data)
            snapshot.update(self._connectivity_fields())
            self.async_set_updated_data(snapshot)

    def set_private_cloud_connected(
        self, connected: bool, error: str | None = None
    ) -> None:
        """Update private-cloud health without blanking cached/live data."""
        self._private_cloud_connected = bool(connected)
        self._last_private_error = error
        if self.data:
            snapshot = dict(self.data)
            snapshot.update(self._connectivity_fields())
            self.async_set_updated_data(snapshot)

    def set_oauth_connected(
        self, connected: bool, error: str | None = None
    ) -> None:
        """Update official OAuth/API health without blanking private data."""
        self._oauth_connected = bool(connected)
        self._last_oauth_error = error
        if self.data:
            snapshot = dict(self.data)
            snapshot.update(self._connectivity_fields())
            self.async_set_updated_data(snapshot)

    def _apply_mqtt_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        position = self._fresh_mqtt_position()
        mqtt_state = (
            _as_int((self._mqtt_location or {}).get("vehicle_state"))
            if position is not None
            else None
        )
        mqtt_action = (
            _as_int((self._mqtt_location or {}).get("action"))
            if position is not None
            else None
        )
        if position is not None:
            snapshot["position"] = position
            snapshot["pose_source"] = "mqtt"
            snapshot["pose_time"] = (self._mqtt_location or {}).get("pose_time")
        snapshot["mqtt_vehicle_state"] = mqtt_state
        snapshot["mqtt_action"] = mqtt_action
        snapshot["trail_active"] = self._is_cutting(
            snapshot.get("state_code"), mqtt_state, mqtt_action
        )
        snapshot.update(self._connectivity_fields())
        snapshot.update(self._navigation_fields(snapshot))
        # MQTT ingestion owns every live route point. A private refresh may
        # still start/finish/update the session while MQTT is fresh, but only
        # contributes a lower-frequency point when no fresh MQTT pose exists.
        history_position = None if position is not None else snapshot.get("position")
        self._update_history(snapshot, history_position, mqtt_state, mqtt_action)
        snapshot["trail"] = self.history.active_points_xy()
        snapshot["trail_session"] = self.history.active_session_no
        snapshot["trail_started_at"] = self.history.active_started_at()
        snapshot["sessions"] = self.history.session_summaries(include_points=False)
        return snapshot

    def set_mqtt_connected(
        self,
        connected: bool,
        *,
        configured: bool = True,
        error: str | None = None,
    ) -> None:
        """Update official OAuth/MQTT health without blanking private data."""
        self._mqtt_configured = configured
        self._mqtt_connected = bool(connected)
        if connected:
            self._oauth_connected = True
            self._last_oauth_error = None
            self._last_mqtt_error = None
        elif error:
            self._last_mqtt_error = error
        if self.data:
            snapshot = dict(self.data)
            snapshot.update(self._connectivity_fields())
            self.async_set_updated_data(snapshot)

    def ingest_mqtt_location(self, location: dict[str, Any]) -> None:
        """Merge one official MQTT pose and persist the full session path."""
        if not isinstance(location, dict):
            return
        self._mqtt_location = dict(location)
        pose_updated = bool(location.get("_pose_updated")) or (
            self._mqtt_last_update is None
            and _as_float(location.get("x")) is not None
            and _as_float(location.get("y")) is not None
        )
        if pose_updated:
            # Progress/zone/state packets carry cached X/Y as context. They must
            # not refresh pose age or make a stalled location stream look live.
            self._mqtt_last_update = time.monotonic()
        self._mqtt_connected = True
        position = self._fresh_mqtt_position()
        mqtt_state = _as_int(location.get("vehicle_state"))
        mqtt_action = _as_int(location.get("action"))
        snapshot = dict(self.data or self._bootstrap_snapshot())
        state_code = str(snapshot.get("state_code") or "")
        if position is not None:
            snapshot["position"] = position
            snapshot["pose_source"] = "mqtt"
            snapshot["pose_time"] = location.get("pose_time")
        snapshot["mqtt_location"] = dict(location)
        snapshot["mqtt_vehicle_state"] = mqtt_state
        snapshot["mqtt_action"] = mqtt_action
        cutting = self._is_cutting(state_code, mqtt_state, mqtt_action)
        snapshot["trail_active"] = cutting
        if cutting:
            snapshot["activity"] = ACTIVITY_MOWING
        elif mqtt_state == MQTT_STATE_RETURNING:
            snapshot["activity"] = ACTIVITY_RETURNING
        elif mqtt_state in MQTT_DOCKED_STATES:
            snapshot["activity"] = ACTIVITY_DOCKED
        snapshot["mow_route_progress"] = (
            _as_float(location.get("mow_progress")) / 100.0
            if location.get("mow_progress") is not None
            else snapshot.get("mow_route_progress")
        )
        snapshot.update(self._connectivity_fields())
        snapshot.update(self._navigation_fields(snapshot))
        # Only a new pose packet contributes a route point. Progress, target-
        # zone and delay packets still update the session state, but their
        # cached X/Y must not create a false sample with an old pose timestamp.
        self._update_history(
            snapshot, position if pose_updated else None, mqtt_state, mqtt_action
        )
        snapshot["trail"] = self.history.active_points_xy()
        snapshot["trail_session"] = self.history.active_session_no
        snapshot["trail_started_at"] = self.history.active_started_at()
        snapshot["sessions"] = self.history.session_summaries(include_points=False)
        self.async_set_updated_data(snapshot)

    def channel_state(self, channel: NavimowerChannel) -> bool | None:
        """Return channel membership, or None when the pose is stale/missing."""
        position = self._fresh_mqtt_position()
        if position is None:
            return None
        return channel.contains(position.get("x"), position.get("y"))

    def _map_payload_with_sessions(
        self, sessions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Build the versioned payload consumed by Navimower Map Card."""
        data = self.data or self._bootstrap_snapshot()
        entry_id = self.entry.entry_id
        map_data = data.get("map") or self._map_snapshot(self._map_geometry or {})
        active = self.history.active_session
        active_meta = None
        if active is not None:
            active_meta = {
                key: active.get(key)
                for key in (
                    "id",
                    "sequence",
                    "started_at",
                    "ended_at",
                    "active",
                    "mode",
                    "zone_ids",
                    "cutting_height_mm",
                    "completed",
                )
            }
            active_meta["point_count"] = len(active.get("points") or [])
        return {
            "schema_version": MAP_API_SCHEMA_VERSION,
            "entry_id": entry_id,
            "vehicle_sn_masked": (
                f"{self.sn[:3]}***{self.sn[-4:]}" if len(self.sn) >= 8 else "***"
            ),
            "map": map_data,
            "coverage": data.get("coverage"),
            "zone_details": data.get("zone_details") or [],
            "cut_height": (data.get("settings") or {}).get("cut_height"),
            "cutting_height_mm": (data.get("settings") or {}).get("cut_height"),
            "doodles": (map_data or {}).get("doodles") or [],
            "trail": self.history.active_trail_xy(),
            "trail_session": self.history.active_session_no,
            "trail_started_at": self.history.active_started_at(),
            "trail_active": bool(data.get("trail_active")),
            "active_session": active_meta,
            "sessions": sessions,
            "session_xy_point_format": list(SESSION_CARD_POINT_FORMAT),
            "session_detail_point_format": list(SESSION_DETAIL_POINT_FORMAT),
            "sessions_api_path": f"/api/navimower/sessions/{entry_id}",
            "session_api_path_template": (
                f"/api/navimower/session/{entry_id}/{{session_id}}"
            ),
            "activity": data.get("activity"),
            "current_physical_zone": data.get("current_physical_zone"),
            "target_zone": data.get("target_zone"),
            "current_tunnel": data.get("current_tunnel"),
            "channels": [channel.as_dict() for channel in self.channels],
            "gates": [gate.as_dict() for gate in self.gates],
        }

    def map_payload(self) -> dict[str, Any]:
        """Return a synchronous payload using currently cached session data."""
        return self._map_payload_with_sessions(
            self.history.session_summaries(include_points=True)
        )

    async def async_map_payload(self) -> dict[str, Any]:
        """Return the live map plus every session retained by the user policy."""
        sessions = await self.history.async_card_sessions()
        return self._map_payload_with_sessions(sessions)

    def sessions_payload(self) -> dict[str, Any]:
        """Return lightweight retained-session metadata."""
        return self.history.sessions_index_payload()

    async def async_session_payload(
        self, session_id: str
    ) -> dict[str, Any] | None:
        """Return one full timestamped session without account identifiers."""
        payload = await self.history.async_session_payload(session_id)
        if payload is None:
            return None
        payload.pop("sn", None)
        payload["point_format"] = list(SESSION_DETAIL_POINT_FORMAT)
        return payload

    @staticmethod
    def _parse_maintenance(maintenance: Any) -> dict:
        """Blades/chassis life from get-component-maintenance.

        The endpoint returns ``{knife:{setTime,usedTime}, chassis:{...}, ...}``.
        Interpretation (best-effort, documented): ``setTime`` is the user's
        maintenance-reminder interval in HOURS, ``usedTime`` the component
        runtime in MINUTES, so::

            life% = clamp(100 * (1 - usedTime / (setTime * 60)), 0, 100)

        e.g. knife 80h/749min -> 84%, chassis 200h/749min -> 94%. The raw
        setTime/usedTime are surfaced as sensor attributes.
        """
        result = {
            "blades_pct": None,
            "blades_set_hours": None,
            "blades_used_min": None,
            "chassis_pct": None,
            "chassis_set_hours": None,
            "chassis_used_min": None,
        }
        if not isinstance(maintenance, dict):
            return result

        def life(component: Any) -> tuple[int | None, int | None, int | None]:
            if not isinstance(component, dict):
                return None, None, None
            set_hours = _as_float(component.get("setTime"))
            used_min = _as_float(component.get("usedTime"))
            pct = None
            if set_hours is not None and used_min is not None and set_hours > 0:
                pct = 100.0 * (1.0 - used_min / (set_hours * 60.0))
                pct = round(max(0.0, min(100.0, pct)))
            return pct, _as_int(set_hours), _as_int(used_min)

        result["blades_pct"], result["blades_set_hours"], result["blades_used_min"] = life(
            maintenance.get("knife")
        )
        result["chassis_pct"], result["chassis_set_hours"], result["chassis_used_min"] = life(
            maintenance.get("chassis")
        )
        return result

    @staticmethod
    def _parse_position(location: Any) -> dict | None:
        x = _as_float(_find(location, "posture_x", "postureX", "last_posture_x", "x"))
        y = _as_float(_find(location, "posture_y", "postureY", "last_posture_y", "y"))
        # posture_theta is the real heading field (radians); keep older guesses.
        heading = _as_float(
            _find(
                location,
                "posture_theta",
                "last_posture_theta",
                "posture_yaw",
                "yaw",
                "heading",
                "angle",
            )
        )
        if x is None and y is None:
            return None
        return {"x": x, "y": y, "heading": heading}

    @staticmethod
    def _parse_path(location: Any) -> list | None:
        points = _find(location, "points", "path", "trail")
        if isinstance(points, list) and points:
            return points
        return None

    # -------------------------------------------------------------- commands
    async def async_send(self, func, *args) -> Any:
        """Run a client command in the executor then request a quick refresh."""
        result = await self.hass.async_add_executor_job(func, *args)
        self._persist_session()
        await self.async_request_refresh()
        return result
