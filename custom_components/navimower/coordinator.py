"""DataUpdateCoordinator for Navimower.

Polls the private cloud on a fixed interval and produces a single, defensively
parsed snapshot dict consumed by every entity. All blocking work (crypto + IO)
runs in one executor job per cycle so the event loop is never blocked.
"""
from __future__ import annotations

import base64
from copy import deepcopy
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
from .map_identifiers import resolve_map_identifiers
from .zone_state import build_zone_model, zone_model_signature
from .const import (
    ACTIVE_STATES,
    ACTIVITY_DOCKED,
    ACTIVITY_ERROR,
    ACTIVITY_MOWING,
    ACTIVITY_PAUSED,
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
    COMMAND_ACTIVITY_TTL_SECONDS,
    COMMAND_TARGET_TTL_SECONDS,
    CYCLE_RESET_STALE_GUARD_SECONDS,
    CUTTING_HEIGHT_MAX_MM,
    CUTTING_HEIGHT_MIN_MM,
    DEFAULT_LANGUAGE,
    DEFAULT_SCAN_INTERVAL,
    DOCKED_STATES,
    DOMAIN,
    FAST_SCAN_INTERVAL,
    GATE_ARRIVAL_GUARD_SECONDS,
    MOW_SCAN_INTERVAL,
    MQTT_CUTTING_ACTIONS,
    MQTT_DOCKED_STATES,
    MQTT_STATE_IDLE,
    MQTT_STATE_MAPPING,
    MQTT_STATE_MOWING,
    MQTT_STATE_RETURNING,
    MQTT_POSE_STALE_SECONDS,
    MQTT_STATE_STALE_SECONDS,
    MQTT_TELEMETRY_STALE_SECONDS,
    MQTT_HISTORY_SAVE_DELAY_SECONDS,
    OPT_CHANNELS,
    OPT_GATES,
    OPT_INCLUDE_RETURN_TRAIL,
    OPT_TRAIL_RETENTION_DAYS,
    OPT_ZONES,
    DEFAULT_TRAIL_RETENTION_DAYS,
    MAP_API_SCHEMA_VERSION,
    MAP_GEOMETRY_SCHEMA_VERSION,
    PRIVATE_CORE_HEALTH_SECONDS,
    PRIVATE_ENDPOINT_TTLS_ACTIVE,
    PRIVATE_ENDPOINT_TTLS_IDLE,
    PRIVATE_FAST_REFRESH_MIN_SECONDS,
    STATE_MOWING,
    STATE_RETURNING,
    TUNNEL_DETECTION_RADIUS_M,
    ZONE_EDGE_TOLERANCE_M,
    VEHICLE_STATE_LABELS,
    VEHICLE_STATE_TO_ACTIVITY,
    VENDOR_COMPLETION_PROGRESS_MIN,
    decode_partition_id_list,
    encode_partition_ids,
)

_LOGGER = logging.getLogger(__name__)

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


def _progress_percent(value: Any) -> int | None:
    """Normalize ratio, percent or basis-point progress to 0..100 percent."""
    parsed = _as_float(value)
    if parsed is None or parsed < 0:
        return None
    if 0 < parsed < 1:
        parsed *= 100
    elif parsed > 100:
        parsed /= 100
    if parsed < 0 or parsed > 100:
        return None
    return int(round(parsed))


def _normalize_cutting_height_mm(value: Any) -> int | None:
    """Return a plausible remote cutting height, not encoded firmware data."""
    parsed = _as_int(value)
    if parsed is None:
        return None
    if CUTTING_HEIGHT_MIN_MM <= parsed <= CUTTING_HEIGHT_MAX_MM:
        return parsed
    return None


def _apply_gate_arrival_guard(
    *,
    target_ids: list[int],
    target_source: str,
    physical_zone_id: int | None,
    guards: dict[str, dict[str, Any]],
    now_monotonic: float,
    command_fresh: bool,
    is_returning: bool,
) -> tuple[list[int], str, dict[str, Any] | None]:
    """Suppress a stale reverse target immediately after a gate arrival."""
    if command_fresh or is_returning or physical_zone_id is None or len(target_ids) != 1:
        return target_ids, target_source, None
    if target_source not in {
        "mqtt_work_target", "mqtt_partition_ids", "private_current_zones",
        "private_work_target", "last_known",
    }:
        return target_ids, target_source, None
    target_id = target_ids[0]
    for slug, guard in guards.items():
        arrived_at = _as_float(guard.get("arrived_at"))
        from_id = _as_int(guard.get("from_zone_id"))
        to_id = _as_int(guard.get("to_zone_id"))
        if arrived_at is None or from_id is None or to_id is None:
            continue
        age = now_monotonic - arrived_at
        if 0 <= age <= GATE_ARRIVAL_GUARD_SECONDS:
            if physical_zone_id == to_id and target_id == from_id:
                held = dict(guard)
                held["slug"] = slug
                held["age_seconds"] = round(age, 1)
                return [to_id], "gate_arrival_guard", held
    return target_ids, target_source, None


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


def _dedupe_zone_ids(values: Any) -> list[int]:
    """Return positive integer zone ids in stable order."""
    result: list[int] = []
    for item in values or []:
        value = _as_int(item)
        if value is not None and value > 0 and value not in result:
            result.append(value)
    return result


def _extract_command_number(value: Any) -> str | None:
    """Extract the vendor command number from any known response shape."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text or None
    if isinstance(value, dict):
        for key in (
            "cmd_num",
            "cmdNum",
            "command_num",
            "commandNum",
            "command_number",
            "commandNumber",
        ):
            if key in value:
                found = _extract_command_number(value.get(key))
                if found is not None:
                    return found
        for nested in value.values():
            if isinstance(nested, (dict, list, tuple)):
                found = _extract_command_number(nested)
                if found is not None:
                    return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _extract_command_number(nested)
            if found is not None:
                return found
    return None


def _command_target_is_fresh(
    set_at: float | None,
    now: float,
    ttl: int = COMMAND_TARGET_TTL_SECONDS,
) -> bool:
    """Return whether a locally issued target intent is still authoritative."""
    return set_at is not None and 0 <= now - set_at <= ttl


def _resolve_navigation_target_ids(
    *,
    is_docked: bool,
    is_returning: bool,
    dock_zone_id: int | None,
    physical_zone_id: int | None,
    command_target_ids: list[int],
    command_target_fresh: bool,
    mqtt_work_target: int | None,
    cloud_work_target: int | None,
    mqtt_partition_ids: list[int],
    cloud_zone_ids: list[int],
    last_target_ids: list[int],
) -> tuple[list[int], str, bool]:
    """Resolve the best target and its source without trusting stale origin data.

    The direct HA command wins while fresh. A packed immediate target that still
    equals the physical origin is treated as stale when a single, newer task zone
    points elsewhere. This is the common start-up transition seen after a mower is
    sent from one side of a gate to the other.
    """
    if is_returning and dock_zone_id is not None:
        return [dock_zone_id], "returning_to_dock", False
    if is_docked:
        return [], "docked", False

    command_ids = _dedupe_zone_ids(command_target_ids)
    command_target = command_ids[0] if command_ids else None
    command_confirmed = bool(
        command_target_fresh
        and command_target is not None
        and physical_zone_id == command_target
        and command_target in {mqtt_work_target, cloud_work_target}
    )
    if command_target_fresh and command_target is not None:
        return [command_target], (
            "ha_command_confirmed" if command_confirmed else "ha_command"
        ), command_confirmed

    mqtt_ids = _dedupe_zone_ids(mqtt_partition_ids)
    cloud_ids = _dedupe_zone_ids(cloud_zone_ids)
    task_ids = mqtt_ids or cloud_ids

    def _stale_origin(value: int | None) -> bool:
        return bool(
            value is not None
            and physical_zone_id is not None
            and value == physical_zone_id
            and len(task_ids) == 1
            and task_ids[0] != physical_zone_id
        )

    if (
        mqtt_work_target is not None
        and mqtt_work_target > 0
        and not _stale_origin(mqtt_work_target)
    ):
        return [mqtt_work_target], "mqtt_work_target", False
    if mqtt_ids:
        return mqtt_ids, "mqtt_partition_ids", False
    if cloud_ids:
        return cloud_ids, "private_current_zones", False
    if (
        cloud_work_target is not None
        and cloud_work_target > 0
        and not _stale_origin(cloud_work_target)
    ):
        return [cloud_work_target], "private_work_target", False

    last_ids = _dedupe_zone_ids(last_target_ids)
    return (
        (last_ids, "last_known", False)
        if last_ids
        else ([], "none", False)
    )


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
        "off_limit_areas": polygons("obstacles"),
        "vf_off_areas": polygons("vision_off_areas"),
        "channels": tunnels,
        "doodles": doodles,
        "terrain_sense": deepcopy_json(geom.get("terrain_sense") or []),
        "station": station,
    }


def _normalize_cached_geometry(geometry: dict[str, Any]) -> dict[str, Any]:
    """Migrate persisted pre-v0.2.3 geometry keys without losing offline data."""
    normalized = deepcopy_json(geometry)
    if not isinstance(normalized, dict):
        normalized = dict(geometry)

    if "off_limit_areas" not in normalized:
        normalized["off_limit_areas"] = normalized.get("obstacles") or []
    if "vf_off_areas" not in normalized:
        normalized["vf_off_areas"] = (
            normalized.get("vision_off_areas")
            or normalized.get("vision_off")
            or []
        )
    if "channels" not in normalized:
        normalized["channels"] = normalized.get("tunnels") or []

    # Keep only the current public names in the restored snapshot.
    normalized.pop("obstacles", None)
    normalized.pop("vision_off_areas", None)
    normalized.pop("vision_off", None)
    normalized.pop("tunnels", None)
    return normalized


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
    # startPlan is the app's global schedule master switch. The weekly plan is
    # retained when disabled, but no next event should be advertised.
    if _as_bool(_find(set_list, "startPlan", "start_plan")) is False:
        return None
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
        self._raw_cache: dict[str, Any] = {}
        self._endpoint_status: dict[str, dict[str, Any]] = {}
        self._last_private_attempt_mono: float | None = None
        self._last_private_success_mono: float | None = None
        self._last_private_core_success_mono: float | None = None
        self._last_private_poll_had_success = False
        self._last_private_poll_had_core_success = False
        self._last_fast_refresh_request_mono = 0.0
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
        self._mqtt_last_message_update: float | None = None
        self._mqtt_state_last_update: float | None = None
        self._mqtt_action_last_update: float | None = None
        self._mqtt_battery: int | None = None
        self._mqtt_battery_last_update: float | None = None
        self._mqtt_progress_last_update: float | None = None
        self._mqtt_area_last_update: float | None = None
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
        self._gate_arrival_guards: dict[str, dict[str, Any]] = {}
        self._last_docked_source: str | None = None
        self._last_target_zone_ids: list[int] = []
        self._command_target_zone_ids: list[int] = []
        self._command_target_set_at: float | None = None
        self._command_target_source: str | None = None
        self._last_mow_command_trace: dict[str, Any] | None = None
        self._pending_activity: str | None = None
        self._pending_activity_set_at: float | None = None
        self._last_physical_zone_id: int | None = None
        self._last_physical_zone_name: str | None = None
        self._last_channel_state: str | None = None
        self._last_channel_id: int | None = None
        self._last_channel_connection: list[int] = []
        self._last_channel_distance: float | None = None
        self._progress_reset_pending = False
        self._coverage_reset_pending = False
        self._area_reset_pending = False
        self._cycle_reset_started_mono: float | None = None
        self._cycle_reset_reason: str | None = None
        self._cycle_reset_previous_area: float | None = None
        self._restored_telemetry: dict[str, Any] = {}
        self._private_reauth_started = False
        self._gate_release_tasks: dict[str, Any] = {}
        self._shutdown_complete = False
        self._zone_states_revision = 0
        self._zone_states_signature: tuple[Any, ...] | None = None
        self._daily_trails_cache_key: tuple[Any, ...] | None = None
        self._daily_trails_cache: dict[str, Any] | None = None

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
                self._map_geometry = _normalize_cached_geometry(geometry)
            key = cached.get("map_cache_key")
            geometry_schema = _as_int(cached.get("geometry_schema"))
            if (
                geometry_schema == MAP_GEOMETRY_SCHEMA_VERSION
                and isinstance(key, (list, tuple))
                and len(key) == 3
            ):
                self._map_cache_key = tuple(str(value) for value in key)
            else:
                # Keep normalized cached geometry for immediate bootstrap display,
                # but force one cloud re-decode whenever the persisted geometry
                # schema changes.
                self._map_cache_key = None
            telemetry = cached.get("telemetry")
            if isinstance(telemetry, dict):
                self._restored_telemetry = dict(telemetry)

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
        cached_raw_heights = [
            _as_int((zone.get("boundary") or {}).get("height_set"))
            for zone in map_geometry.get("zones") or []
            if isinstance(zone, dict)
        ]
        cached_height_supported = bool(
            any(_normalize_cutting_height_mm(value) is not None for value in cached_raw_heights)
            and not any(
                value not in (None, 0, 256)
                and _normalize_cutting_height_mm(value) is None
                for value in cached_raw_heights
            )
        )
        map_payload = (
            self._map_snapshot(
                map_geometry,
                cutting_height_supported=cached_height_supported,
            )
            if map_geometry
            else None
        )
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
            "battery": _as_int(self._restored_telemetry.get("battery")),
            "battery_source": self._restored_telemetry.get("battery_source")
            or "persisted_last_known",
            # Legacy raw fields remain internal fallbacks for one release cycle,
            # but restored public values use the v0.3 zone/task terminology.
            "mowing_progress": _progress_percent(
                self._restored_telemetry.get("task_progress")
            ),
            "session_area": _as_float(
                self._restored_telemetry.get("task_mowed_area")
            ),
            "total_area": (
                _as_float(self._restored_telemetry.get("map_area"))
                if _as_float(self._restored_telemetry.get("map_area")) is not None
                else _as_float(map_geometry.get("area"))
            ),
            "zones": zones,
            "current_zone": None,
            "current_zone_ids": [],
            "coverage": None,
            "settings": {
                "schedule_enabled": None,
                "cut_height": None,
                "cut_height_raw": None,
                "cutting_height_supported": cached_height_supported,
            },
            "cutting_height_supported": cached_height_supported,
            "maintenance": {},
            "position": None,
            "map": map_payload,
            "zone_details": self._merge_zone_history(
                self._build_zone_details(None, None, cached_height_supported)
            ),
            "zone_states": [],
            "zone_states_revision": 0,
            "totals": {},
            "trail": self.history.active_points_xy(),
            "sessions": self.history.session_summaries(include_points=False),
            "trail_active": self.history.active_session is not None,
            "gate_areas": [channel.as_dict() for channel in self.channels],
            "gates": [gate.as_dict() for gate in self.gates],
            "raw": {},
        }
        snapshot.update(self._connectivity_fields())
        snapshot.update(self._navigation_fields(snapshot))
        self._refresh_zone_model(snapshot)
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

    def _state_store_data(
        self, snapshot: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = snapshot or self.data or {}
        return {
            "sn": self.sn,
            "geometry_schema": MAP_GEOMETRY_SCHEMA_VERSION,
            "map_geometry": self._map_geometry,
            "map_cache_key": list(self._map_cache_key)
            if self._map_cache_key
            else None,
            "telemetry": {
                "battery": data.get("battery"),
                "battery_source": data.get("battery_source"),
                "task_progress": (data.get("totals") or {}).get("task_progress_pct"),
                "task_mowed_area": (data.get("totals") or {}).get("task_mowed_area_m2"),
                "map_coverage": (data.get("totals") or {}).get("map_coverage_pct"),
                "map_mowed_area": (data.get("totals") or {}).get("map_mowed_area_m2"),
                "map_area": (data.get("totals") or {}).get("map_area_m2"),
            },
        }

    # ------------------------------------------------------------------ poll
    async def _async_update_data(self) -> dict:
        """Refresh private-cloud data while preserving every last-good value.

        Endpoint failures are isolated inside :meth:`_fetch_blocking`. Only a
        rejected private-cloud session or the absence of any usable core data
        reaches this outer handler.
        """
        try:
            snapshot = await self.hass.async_add_executor_job(self._fetch_blocking)
        except (NavimowAuthError, NavimowError) as err:
            self._private_cloud_connected = False
            self._last_private_error = str(err)
            if isinstance(err, NavimowAuthError) and not self._private_reauth_started:
                self._private_reauth_started = True
                self.entry.async_start_reauth(
                    self.hass, data={"reauth_type": "private"}
                )
            snapshot = dict(self.data or self._bootstrap_snapshot())
            self.update_interval = timedelta(
                seconds=self._poll_interval_for_snapshot(snapshot)
            )
            snapshot.update(self._connectivity_fields())
            return snapshot

        core_age = self.private_core_age()
        self._private_cloud_connected = (
            core_age is not None and core_age <= PRIVATE_CORE_HEALTH_SECONDS
        )
        self._private_reauth_started = False
        self._last_private_error = self._private_error_summary()
        self._persist_session()
        snapshot = self._apply_mqtt_snapshot(snapshot)
        self.history.update_from_snapshot(snapshot)
        snapshot["zone_details"] = self._merge_zone_history(
            snapshot.get("zone_details") or []
        )
        self._refresh_zone_model(snapshot)
        snapshot["trail"] = self.history.active_points_xy()
        snapshot["trail_session"] = self.history.active_session_no
        snapshot["trail_started_at"] = self.history.active_started_at()
        snapshot["sessions"] = self.history.session_summaries(include_points=False)

        if self._map_dirty:
            self._map_dirty = False
        self._schedule_state_save(snapshot)

        self.update_interval = timedelta(
            seconds=self._poll_interval_for_snapshot(snapshot)
        )
        snapshot.update(self._connectivity_fields())
        return snapshot

    @staticmethod
    def _poll_interval_for_snapshot(snapshot: dict[str, Any]) -> int:
        activity = snapshot.get("activity")
        code = str(snapshot.get("state_code") or "")
        mqtt_state = _as_int(snapshot.get("mqtt_vehicle_state"))
        if (
            activity == ACTIVITY_MOWING
            or code == STATE_MOWING
            or mqtt_state == MQTT_STATE_MOWING
        ):
            return MOW_SCAN_INTERVAL
        if (
            activity == ACTIVITY_RETURNING
            or code in ACTIVE_STATES
            or mqtt_state in {MQTT_STATE_RETURNING, MQTT_STATE_MAPPING}
        ):
            return FAST_SCAN_INTERVAL
        return DEFAULT_SCAN_INTERVAL

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

    def _refresh_zone_model(self, snapshot: dict[str, Any]) -> None:
        """Build the one authoritative zone/totals model used by HA and the card."""
        map_data = snapshot.get("map") or self._map_snapshot(
            self._map_geometry or {},
            cutting_height_supported=snapshot.get("cutting_height_supported"),
        )
        map_zones = [
            dict(item) for item in (map_data or {}).get("zones") or []
            if isinstance(item, dict)
        ]
        active_zone_id = _as_int(snapshot.get("active_zone_progress_zone_id"))
        if active_zone_id is None:
            active_zone_id = _as_int(snapshot.get("current_physical_zone_id"))
        if active_zone_id is None:
            candidates = snapshot.get("current_zone_ids") or []
            if len(candidates) == 1:
                active_zone_id = _as_int(candidates[0])
        zone_states, totals = build_zone_model(
            map_zones=map_zones,
            zone_details=[
                dict(item) for item in snapshot.get("zone_details") or []
                if isinstance(item, dict)
            ],
            coverage=snapshot.get("coverage"),
            zone_history=self.history.zone_history(),
            active_session=self.history.active_session,
            active_zone_id=active_zone_id,
            task_progress_pct=snapshot.get("mowing_progress"),
            task_mowed_area_m2=snapshot.get("session_area"),
            task_progress_source=snapshot.get("mowing_progress_source"),
            task_area_source=snapshot.get("session_area_source"),
        )
        signature = zone_model_signature(zone_states, totals)
        if signature != self._zone_states_signature:
            self._zone_states_signature = signature
            self._zone_states_revision += 1
        snapshot["zone_states"] = zone_states
        snapshot["zone_states_revision"] = self._zone_states_revision
        snapshot["totals"] = totals
        # Named top-level aliases make diagnostics/templates readable while all
        # public entities are still sourced from the same totals object.
        snapshot["task_progress"] = totals.get("task_progress_pct")
        snapshot["task_mowed_area"] = totals.get("task_mowed_area_m2")
        snapshot["map_coverage"] = totals.get("map_coverage_pct")
        snapshot["map_mowed_area"] = totals.get("map_mowed_area_m2")
        snapshot["map_area"] = totals.get("map_area_m2")
        snapshot["last_map_mowed_at"] = totals.get("last_map_mowed_at")
        snapshot["last_map_completed_at"] = totals.get("last_map_completed_at")
        snapshot["active_cycle_id"] = (self.history.active_session or {}).get("id")

    def _session_completed(self, snapshot: dict[str, Any]) -> bool | None:
        active = self.history.active_session
        if not active:
            return None
        selected = {
            parsed
            for value in active.get("zone_ids") or []
            if (parsed := _as_int(value)) is not None
        }
        task_progress = active.get("task_zone_progress") or {}
        if selected and task_progress:
            values = [_progress_percent(task_progress.get(str(zone_id))) for zone_id in selected]
            known = [value for value in values if value is not None]
            if len(known) == len(selected):
                return all(value >= VENDOR_COMPLETION_PROGRESS_MIN for value in known)
        percentages = {
            _as_int(item.get("id")): _progress_percent(
                item.get("progress")
                if item.get("progress") is not None
                else item.get("percentage")
            )
            for item in snapshot.get("zone_details") or []
            if isinstance(item, dict) and _as_int(item.get("id")) is not None
        }
        for item in (snapshot.get("coverage") or {}).get("zones") or []:
            if not isinstance(item, dict):
                continue
            zone_id = _as_int(item.get("id"))
            if zone_id is None or percentages.get(zone_id) is not None:
                continue
            percentages[zone_id] = _progress_percent(item.get("pct"))
        relevant = (
            [percentages.get(zone_id) for zone_id in selected]
            if selected
            else list(percentages.values())
        )
        known = [value for value in relevant if value is not None]
        return all(value >= VENDOR_COMPLETION_PROGRESS_MIN for value in known) if known else None

    def _active_cutting_height(self, snapshot: dict[str, Any]) -> int | None:
        if snapshot.get("cutting_height_supported") is False:
            return None
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
            height = _normalize_cutting_height_mm(detail.get("cutting_height_mm"))
            if height is not None:
                return height
        return _normalize_cutting_height_mm(
            (snapshot.get("settings") or {}).get("cut_height")
        )

    def _update_history(
        self,
        snapshot: dict[str, Any],
        position: dict[str, Any] | None,
        mqtt_state: int | None,
        mqtt_action: int | None,
    ) -> None:
        cutting = self._is_cutting(snapshot.get("state_code"), mqtt_state, mqtt_action)
        docked = bool(snapshot.get("docked"))
        returning = self._is_returning_state(
            snapshot.get("state_code"), mqtt_state
        )
        zone_ids = list(
            dict.fromkeys(
                parsed
                for value in [
                    *(snapshot.get("current_zone_ids") or []),
                    *(snapshot.get("target_zone_ids") or []),
                ]
                if (parsed := _as_int(value)) is not None
            )
        )
        active_height = self._active_cutting_height(snapshot)
        snapshot["active_cutting_height_mm"] = active_height
        cycle_reset = self.history.prepare_cycle(
            snapshot,
            pose_time=snapshot.get("pose_time"),
        )
        snapshot["cycle_reset_detected"] = cycle_reset
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
            physical_zone_id=_as_int(snapshot.get("current_physical_zone_id")),
            completed=self._session_completed(snapshot) if docked else None,
        )

    def _fetch_blocking(self) -> dict:
        """Fetch due private-cloud endpoints and parse one merged snapshot.

        Each endpoint owns its own TTL and last-good cache. A timeout from one
        endpoint therefore never erases values supplied by another endpoint and
        does not turn every entity unavailable.
        """
        sn, vtype = self.sn, self.vehicle_type
        raw = self._raw_cache
        now = time.monotonic()
        self._last_private_attempt_mono = now
        active = self._private_poll_active()
        ttls = (
            PRIVATE_ENDPOINT_TTLS_ACTIVE
            if active
            else PRIVATE_ENDPOINT_TTLS_IDLE
        )
        getters: dict[str, Any] = {
            "device_info": lambda: self.client.device_info(sn),
            "index2": lambda: self.client.index2(sn),
            "auth_list": self.client.auth_list,
            "location": lambda: self.client.location(sn, vtype),
            "path_info_time": lambda: self.client.path_info_time(sn),
            "set_list": lambda: self.client.set_list(sn),
            "maintenance": lambda: self.client.maintenance(sn),
            "today_plan": lambda: self.client.today_plan(sn, vtype),
            "map_list": lambda: self.client.map_list(sn),
        }

        successes: set[str] = set()
        for key, getter in getters.items():
            if self._fetch_endpoint(
                raw,
                key,
                getter,
                ttl=int(ttls.get(key, DEFAULT_SCAN_INTERVAL)),
                now=now,
            ):
                successes.add(key)

        # A changed location/map-list row is enough to check the map revision.
        # _maybe_fetch_map itself avoids all detail downloads while the revision
        # key is unchanged.
        if (
            self._map_geometry is None
            or "location" in successes
            or "map_list" in successes
        ):
            try:
                self._maybe_fetch_map(raw)
            except NavimowAuthError:
                raise
            except Exception as err:  # noqa: BLE001 - keep cached geometry.
                _LOGGER.warning(
                    "Navimower map refresh failed; keeping cached geometry: %s",
                    err,
                )

        core_keys = {"index2", "auth_list", "location"}
        core_success = bool(successes & core_keys)
        self._last_private_poll_had_success = bool(successes)
        self._last_private_poll_had_core_success = core_success
        if successes:
            self._last_private_success_mono = now
        if core_success:
            self._last_private_core_success_mono = now

        has_cached_core = any(raw.get(key) for key in core_keys)
        if not has_cached_core:
            summary = self._private_error_summary() or "No private-cloud core data"
            raise NavimowError(summary)
        return self._parse(raw)

    def _private_poll_active(self) -> bool:
        data = self.data or {}
        activity = data.get("activity")
        state_code = str(data.get("state_code") or "")
        mqtt_state = self._fresh_mqtt_vehicle_state()
        mqtt_active = mqtt_state in {
            MQTT_STATE_MOWING,
            MQTT_STATE_RETURNING,
            MQTT_STATE_MAPPING,
        }
        # A fresh official pose is authoritative at task start, before the
        # private cloud has necessarily switched from docked to mowing.
        if self._fresh_mqtt_position() is not None and mqtt_active:
            return True
        if activity in {ACTIVITY_MOWING, ACTIVITY_RETURNING} or state_code in ACTIVE_STATES:
            return True
        # Once the private cloud definitively reports docked/error, do not keep
        # aggressive polling forever because of an old cached MQTT state=4.
        if activity in {ACTIVITY_DOCKED, ACTIVITY_ERROR} or state_code in DOCKED_STATES:
            return False
        message_age = self.mqtt_message_age()
        return bool(mqtt_active and message_age is not None and message_age <= 45)

    def _fetch_endpoint(
        self,
        raw: dict[str, Any],
        key: str,
        getter: Any,
        *,
        ttl: int,
        now: float,
    ) -> bool:
        status = self._endpoint_status.setdefault(
            key,
            {
                "attempts": 0,
                "successes": 0,
                "failures": 0,
                "last_attempt_mono": None,
                "last_success_mono": None,
                "last_error": None,
                "last_attempt_utc": None,
                "last_success_utc": None,
                "last_error_utc": None,
            },
        )
        last_attempt = status.get("last_attempt_mono")
        due = key not in raw or last_attempt is None or now - last_attempt >= ttl
        if not due:
            return False

        status["attempts"] += 1
        status["last_attempt_mono"] = now
        status["last_attempt_utc"] = datetime.now(UTC).isoformat()
        try:
            value = getter()
            if value is None:
                raise NavimowError(f"{key} returned no data")
        except NavimowAuthError as err:
            status["failures"] += 1
            status["last_error"] = str(err)
            status["last_error_utc"] = datetime.now(UTC).isoformat()
            raise
        except Exception as err:  # noqa: BLE001 - preserve cache and continue.
            previous_error = status.get("last_error")
            status["failures"] += 1
            status["last_error"] = str(err)
            status["last_error_utc"] = datetime.now(UTC).isoformat()
            if previous_error != str(err) or status["failures"] in {1, 10, 25}:
                _LOGGER.warning(
                    "Navimower private endpoint %s failed; keeping last-good "
                    "data (failure %s): %s",
                    key,
                    status["failures"],
                    err,
                )
            return False

        recovered = bool(status.get("last_error"))
        raw[key] = value
        status["successes"] += 1
        status["last_success_mono"] = now
        status["last_success_utc"] = datetime.now(UTC).isoformat()
        status["last_error"] = None
        status["last_error_utc"] = None
        status["last_result_type"] = type(value).__name__
        if recovered:
            _LOGGER.info("Navimower private endpoint %s recovered", key)
        return True

    def private_poll_age(self) -> float | None:
        if self._last_private_success_mono is None:
            return None
        return max(0.0, time.monotonic() - self._last_private_success_mono)

    def private_core_age(self) -> float | None:
        if self._last_private_core_success_mono is None:
            return None
        return max(0.0, time.monotonic() - self._last_private_core_success_mono)

    def _private_error_summary(self) -> str | None:
        errors = [
            f"{key}: {status.get('last_error')}"
            for key, status in self._endpoint_status.items()
            if status.get("last_error")
        ]
        return "; ".join(errors[:3]) or None

    def polling_diagnostics(self) -> dict[str, Any]:
        """Return JSON-safe private-cloud freshness and endpoint statistics."""
        now = time.monotonic()

        def age(value: Any) -> float | None:
            return (
                round(max(0.0, now - float(value)), 1)
                if value is not None
                else None
            )

        endpoints: dict[str, Any] = {}
        for key, status in self._endpoint_status.items():
            endpoints[key] = {
                "attempts": status.get("attempts", 0),
                "successes": status.get("successes", 0),
                "failures": status.get("failures", 0),
                "last_attempt_utc": status.get("last_attempt_utc"),
                "last_success_utc": status.get("last_success_utc"),
                "last_error_utc": status.get("last_error_utc"),
                "last_error": status.get("last_error"),
                "last_attempt_age_s": age(status.get("last_attempt_mono")),
                "last_success_age_s": age(status.get("last_success_mono")),
                "last_result_type": status.get("last_result_type"),
            }
        return {
            "profile": "active" if self._private_poll_active() else "idle",
            "update_interval_s": (
                self.update_interval.total_seconds()
                if self.update_interval is not None
                else None
            ),
            "last_poll_success_age_s": (
                round(self.private_poll_age(), 1)
                if self.private_poll_age() is not None
                else None
            ),
            "last_core_success_age_s": (
                round(self.private_core_age(), 1)
                if self.private_core_age() is not None
                else None
            ),
            "last_poll_had_success": self._last_private_poll_had_success,
            "last_poll_had_core_success": self._last_private_poll_had_core_success,
            "endpoints": endpoints,
        }

    def _maybe_fetch_map(self, raw: dict) -> None:
        """Fetch + decode the map once, then only when the map version changes."""
        location = raw.get("location") or {}
        map_id, map_base_id, edit_time = resolve_map_identifiers(
            location, raw.get("map_list")
        )
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
        except NavimowAuthError:
            raise
        except NavimowError:
            geometry = None
        if geometry is None:
            try:
                blob = self.client.map_detail(self.sn, str(map_id), str(map_base_id))
                geometry = _parse_map_detail(blob)
            except NavimowAuthError:
                raise
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
        except NavimowAuthError:
            raise
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
    def _map_snapshot(
        map_geometry: dict[str, Any],
        *,
        cutting_height_supported: bool | None = None,
    ) -> dict[str, Any]:
        """Return the stable map object exposed to entities and HTTP API."""
        zones = deepcopy_json(map_geometry.get("zones") or []) or []
        if cutting_height_supported is False:
            for zone in zones:
                if not isinstance(zone, dict):
                    continue
                boundary = zone.get("boundary")
                if isinstance(boundary, dict):
                    boundary.pop("height_set", None)
        return {
            "id": map_geometry.get("id"),
            "map_id": map_geometry.get("map_id"),
            "map_base_id": map_geometry.get("map_base_id"),
            "edit_time": map_geometry.get("edit_time"),
            "revision": map_geometry.get("revision"),
            "name": map_geometry.get("name"),
            "area": map_geometry.get("area"),
            "zones": zones,
            "off_limit_areas": map_geometry.get("off_limit_areas") or [],
            "vf_off_areas": map_geometry.get("vf_off_areas") or [],
            "channels": map_geometry.get("channels") or [],
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
        self,
        coverage: dict[str, Any] | None,
        global_height: int | None,
        cutting_height_supported: bool,
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
            normalized_height = _normalize_cutting_height_mm(raw_height)
            known_inherit = raw_height in (None, 0, 256)
            unknown_encoded = (
                raw_height not in (None, 0, 256) and normalized_height is None
            )
            configured_height = None if known_inherit or unknown_encoded else normalized_height
            effective_height = None
            if cutting_height_supported:
                effective_height = (
                    global_height
                    if known_inherit or unknown_encoded
                    else configured_height
                )
            inherits = True if known_inherit else None if unknown_encoded else False
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
                        if end_time
                        and (percentage or 0) >= VENDOR_COMPLETION_PROGRESS_MIN
                        else None
                    ),
                    "cutting_height_supported": cutting_height_supported,
                    "configured_height_raw": raw_height,
                    "configured_height_mm": configured_height,
                    "cutting_height_mm": effective_height,
                    "inherits_global_height": (
                        inherits if cutting_height_supported else None
                    ),
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

    @staticmethod
    def _apply_active_zone_progress(
        zone_details: list[dict[str, Any]],
        *,
        zone_id: int | None,
        progress_candidates: list[tuple[str, Any]],
    ) -> None:
        """Expose the live task progress used by the app for the active zone.

        ``get-path-info-time`` can lag behind the mower's current task. The
        official MQTT route progress and packed work progress are therefore
        preferred for the one active target zone while the vendor percentage is
        retained separately for diagnostics and cycle-reset detection.
        """
        if zone_id is None:
            return
        progress = None
        source = None
        for candidate_source, candidate_value in progress_candidates:
            normalized = _progress_percent(candidate_value)
            if normalized is not None:
                progress = normalized
                source = candidate_source
                break
        if progress is None:
            return
        for detail in zone_details:
            if _as_int(detail.get("id")) != zone_id:
                continue
            detail["vendor_percentage"] = detail.get("percentage")
            detail["progress"] = progress
            detail["progress_source"] = source
            break

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
        private_battery = _as_int(
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
        device_info = raw.get("device_info") or {}
        mowing_extend = _find(device_info, "mowingExtend", "mowing_extend") or {}
        mowing_path_width_raw = _as_float(
            _find(mowing_extend, "mowingPathWidth", "mowing_path_width")
        )
        mowing_path_width_m = None
        if mowing_path_width_raw is not None and mowing_path_width_raw > 0:
            candidate_width = (
                mowing_path_width_raw / 1000.0
                if mowing_path_width_raw > 10
                else mowing_path_width_raw
            )
            if 0.1 <= candidate_width <= 2.0:
                mowing_path_width_m = candidate_width

        # Coverage and position. MQTT is authoritative while fresh; private
        # get-location remains a fallback when MQTT is absent/stale.
        cloud_position = self._parse_position(location)
        coverage = _parse_coverage(raw.get("path_info_time"), zone_names)
        mqtt_position = self._fresh_mqtt_position()
        position = mqtt_position or cloud_position
        mqtt_vehicle_state = self._fresh_mqtt_vehicle_state()
        mqtt_action = self._fresh_mqtt_action()
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
            # cloud state codes. Only a known dock code may default to Docked.
            activity = previous_activity or (
                ACTIVITY_DOCKED if state_code in DOCKED_STATES else ACTIVITY_PAUSED
            )
        pending_activity = self._pending_activity_value()
        if has_error:
            activity = ACTIVITY_ERROR
            self.clear_pending_activity()
        elif pending_activity is not None:
            # Keep the explicit HA command state through short vendor transition
            # codes. Clear it only after a matching known state is observed.
            if activity == pending_activity and (
                mqtt_vehicle_state is not None
                or state_code in VEHICLE_STATE_TO_ACTIVITY
            ):
                self.clear_pending_activity()
            else:
                activity = pending_activity

        state_label = VEHICLE_STATE_LABELS.get(state_code)
        if state_label is None:
            normalized = str(activity or "transitioning").replace("_", " ").title()
            state_label = f"{normalized} ({state_code})" if state_code else normalized

        docked, docked_source = self._resolved_docked_state(
            state_code, mqtt_vehicle_state, activity, pending_activity
        )
        self._last_docked_source = docked_source

        # --- settings (MowerSettingBean; snake_case in set-list, camelCase in bean)
        schedule_enabled = _as_bool(_find(set_list, "startPlan", "start_plan"))
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

        raw_cut_height = _as_int(_find(set_list, "height"))
        normalized_cut_height = _normalize_cutting_height_mm(raw_cut_height)
        raw_zone_heights = [
            _as_int((zone.get("boundary") or {}).get("height_set"))
            for zone in map_geom.get("zones") or []
            if isinstance(zone, dict)
        ]
        zone_height_values = [
            _normalize_cutting_height_mm(value) for value in raw_zone_heights
        ]
        # A vendor-specific/encoded zone value must not disable cutting-height
        # support for the whole mower. Known global/zone heights prove the
        # feature exists; unknown raw zone markers are handled per-zone below.
        cutting_height_supported = bool(
            normalized_cut_height is not None
            or any(value is not None for value in zone_height_values)
        )

        settings = {
            "schedule_enabled": schedule_enabled,
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
            "cut_height": normalized_cut_height,
            "cut_height_raw": raw_cut_height,
            "cutting_height_supported": cutting_height_supported,
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

        private_mowing_progress = _progress_percent(
            _find(location, "mowing_percentage", "mowingPercentage", "progress")
        )
        work_progress = _progress_percent((packed_work or {}).get("progress"))
        mqtt_route_progress = _progress_percent(
            (self._mqtt_location or {}).get("mow_progress")
        )
        zone_details = self._build_zone_details(
            coverage, settings["cut_height"], cutting_height_supported
        )
        active_progress_zone = _as_int((packed_work or {}).get("target_zone"))
        if active_progress_zone is None and len(current_ids) == 1:
            active_progress_zone = current_ids[0]
        if activity == ACTIVITY_MOWING:
            self._apply_active_zone_progress(
                zone_details,
                zone_id=active_progress_zone,
                progress_candidates=[
                    ("map_work_position", work_progress),
                ],
            )

        # --- maintenance (blades / chassis) -- field names are firmware-specific
        maint = self._parse_maintenance(maintenance)

        snapshot: dict[str, Any] = {
            # identity / static
            "vehicle_sn": self.sn,
            "vehicle_type": self.vehicle_type,
            "model": str(auth.get("subType") or _find(raw.get("device_info"), "model") or ""),
            "name": str(auth.get("selfDefinedName") or auth.get("vehicle_name") or "Navimow"),
            # core state
            "battery": private_battery,
            "battery_private_cloud": private_battery,
            "state_code": state_code,
            "state": state_label,
            "activity": activity,
            "mqtt_vehicle_state": mqtt_vehicle_state,
            "mqtt_action": mqtt_action,
            "mqtt_state_age": self.mqtt_state_age(),
            "mqtt_action_age": self.mqtt_action_age(),
            "trail_active": self._is_cutting(state_code, mqtt_vehicle_state, mqtt_action),
            "online": online,
            "docked": docked,
            "docked_source": docked_source,
            "error": has_error,
            "error_text": error_text,
            # progress / areas
            # The cloud exposes separate counters: mowing_percentage is the
            # whole selected task; map_work_position.progress is the immediate
            # active zone/work segment. Keep both meanings explicit.
            "mowing_progress": private_mowing_progress,
            "mowing_progress_private_cloud": private_mowing_progress,
            "task_progress_private_cloud": private_mowing_progress,
            "active_zone_progress": work_progress,
            "active_zone_progress_source": (
                "map_work_position" if work_progress is not None else None
            ),
            "active_zone_progress_zone_id": active_progress_zone,
            "session_area": _as_float(location.get("subtotal_area")),
            "session_area_private_cloud": _as_float(location.get("subtotal_area")),
            "weekly_area": _as_float(location.get("mowing_week_area")),
            "total_area": (
                map_geom.get("area")
                if map_geom.get("area") is not None
                else _as_float(_find(raw.get("device_info"), "map_area_limit"))
            ),
            "total_area_private_cloud": (
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
            "work_progress": work_progress,
            "mow_route_progress": mqtt_route_progress,
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
            "map": self._map_snapshot(
                map_geom, cutting_height_supported=cutting_height_supported
            ) if map_geom else None,
            "cutting_height_supported": cutting_height_supported,
            "mowing_path_width_m": mowing_path_width_m,
            # groups
            "settings": settings,
            "maintenance": maint,
            # source health / local channels
            **self._connectivity_fields(),
            "gate_areas": [channel.as_dict() for channel in self.channels],
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

    @staticmethod
    def _age_since(updated_at: float | None) -> float | None:
        if updated_at is None:
            return None
        return max(0.0, time.monotonic() - updated_at)

    def _private_endpoint_age(self, *keys: str) -> float | None:
        ages: list[float] = []
        now = time.monotonic()
        for key in keys:
            updated = (self._endpoint_status.get(key) or {}).get("last_success_mono")
            if updated is not None:
                ages.append(max(0.0, now - float(updated)))
        return min(ages) if ages else self.private_poll_age()

    def _fresh_mqtt_battery(self) -> int | None:
        age = self._age_since(self._mqtt_battery_last_update)
        if age is None or age > MQTT_TELEMETRY_STALE_SECONDS:
            return None
        value = _as_int(self._mqtt_battery)
        return value if value is not None and 0 <= value <= 100 else None

    def _fresh_mqtt_progress_values(self) -> dict[str, int | None]:
        age = self._age_since(self._mqtt_progress_last_update)
        if age is None or age > MQTT_TELEMETRY_STALE_SECONDS:
            return {
                "mowing_percentage": None,
                "work_progress": None,
                "route_progress": None,
            }
        mqtt = self._mqtt_location or {}
        return {
            "mowing_percentage": _progress_percent(mqtt.get("mowing_percentage")),
            "work_progress": _progress_percent(mqtt.get("work_progress")),
            "route_progress": _progress_percent(mqtt.get("mow_progress")),
        }

    def _mark_display_cycle_reset(
        self,
        reason: str,
        previous: dict[str, Any] | None = None,
    ) -> None:
        """Hold public counters clear until new-cycle values reach the cloud."""
        prior = previous or self.data or {}
        self._progress_reset_pending = True
        self._coverage_reset_pending = False
        self._area_reset_pending = True
        self._cycle_reset_started_mono = time.monotonic()
        self._cycle_reset_reason = reason
        self._cycle_reset_previous_area = _as_float(prior.get("session_area"))

    def _cycle_reset_guard_active(self) -> bool:
        age = self._age_since(self._cycle_reset_started_mono)
        return age is not None and age <= CYCLE_RESET_STALE_GUARD_SECONDS

    @staticmethod
    def _zero_coverage(coverage: dict[str, Any] | None) -> dict[str, Any]:
        base = dict(coverage or {})
        base["overall_pct"] = 0
        base["finished_area"] = 0.0
        base["zones"] = [
            {
                **dict(item),
                "pct": 0,
                "finished": 0.0,
            }
            for item in base.get("zones") or []
            if isinstance(item, dict)
        ]
        return base

    def _stabilize_telemetry(
        self,
        snapshot: dict[str, Any],
        previous_snapshot: dict[str, Any] | None = None,
    ) -> None:
        """Resolve telemetry by freshness and keep one logical cycle monotonic."""
        previous = previous_snapshot or self.data or {}
        activity = str(snapshot.get("activity") or "")
        previous_activity = str(previous.get("activity") or "")
        active = activity in {
            ACTIVITY_MOWING,
            ACTIVITY_PAUSED,
            ACTIVITY_RETURNING,
        }

        previous_coverage = previous.get("coverage")
        previous_coverage_pct = _progress_percent(
            (previous_coverage or {}).get("overall_pct")
            if isinstance(previous_coverage, dict)
            else None
        )
        previous_progress = _progress_percent(previous.get("mowing_progress"))
        completed_before_restart = bool(
            previous_activity
            not in {ACTIVITY_MOWING, ACTIVITY_PAUSED, ACTIVITY_RETURNING}
            and activity == ACTIVITY_MOWING
            and (
                (
                    previous_coverage_pct is not None
                    and previous_coverage_pct >= VENDOR_COMPLETION_PROGRESS_MIN
                )
                or (
                    previous_coverage_pct is None
                    and previous_progress is not None
                    and previous_progress >= VENDOR_COMPLETION_PROGRESS_MIN
                )
            )
        )
        reset_marked = bool(snapshot.get("cycle_reset_detected"))
        if reset_marked:
            self._mark_display_cycle_reset(
                str(snapshot.get("cycle_reset_reason") or "vendor_cycle_reset"),
                previous,
            )
        elif completed_before_restart:
            self._mark_display_cycle_reset("completed_cycle_restart", previous)
            reset_marked = True

        reset_guard = self._cycle_reset_guard_active()

        # Battery: MQTT is the dense source while active; the private cloud is
        # retained as the preferred charging source because it already reports
        # smooth one-percent increments on the dock.
        private_battery = _as_int(snapshot.get("battery_private_cloud"))
        if private_battery is not None and not 0 <= private_battery <= 100:
            private_battery = None
        mqtt_battery = self._fresh_mqtt_battery()
        previous_battery = _as_int(previous.get("battery"))
        charging_context = bool(snapshot.get("docked")) or activity == ACTIVITY_DOCKED
        battery_candidates = (
            [("private_cloud", private_battery), ("mqtt_state", mqtt_battery)]
            if charging_context
            else [("mqtt_state", mqtt_battery), ("private_cloud", private_battery)]
        )
        battery = None
        battery_source = None
        for source, value in battery_candidates:
            if value is not None:
                battery = value
                battery_source = source
                break
        if battery is None and previous_battery is not None:
            battery = previous_battery
            battery_source = "last_known"
        if battery is not None and previous_battery is not None:
            if charging_context and battery < previous_battery:
                battery = previous_battery
                battery_source = "last_known"
            elif active and battery > previous_battery + 1:
                battery = previous_battery
                battery_source = "last_known"
        snapshot["battery"] = battery
        snapshot["battery_source"] = battery_source
        snapshot["battery_private_cloud"] = private_battery
        snapshot["battery_mqtt"] = self._mqtt_battery
        snapshot["battery_mqtt_age"] = self._age_since(
            self._mqtt_battery_last_update
        )
        snapshot["battery_source_age"] = (
            snapshot["battery_mqtt_age"]
            if battery_source == "mqtt_state"
            else self._private_endpoint_age("index2", "auth_list")
            if battery_source == "private_cloud"
            else None
        )

        # Task progress and active-zone progress are separate vendor counters.
        # ``mowingPercentage`` describes the whole selected task. Packed
        # ``mapWorkPosition.progress`` and ``currentMowProgress`` describe the
        # current zone/work route and must never replace the overall task value.
        mqtt_progress = self._fresh_mqtt_progress_values()
        raw_coverage = snapshot.get("coverage")
        private_progress = _progress_percent(
            snapshot.get("task_progress_private_cloud")
            if snapshot.get("task_progress_private_cloud") is not None
            else snapshot.get("mowing_progress_private_cloud")
        )
        progress_candidates = (
            [
                ("mqtt_task_percentage", mqtt_progress["mowing_percentage"]),
                ("private_task_percentage", private_progress),
            ]
            if active
            else [
                ("private_task_percentage", private_progress),
                ("mqtt_task_percentage", mqtt_progress["mowing_percentage"]),
            ]
        )
        progress = None
        progress_source = None
        for source, value in progress_candidates:
            if value is not None:
                progress = value
                progress_source = source
                break
        progress_was_pending = self._progress_reset_pending
        if progress_was_pending and reset_guard:
            if progress is not None and progress <= 25:
                self._progress_reset_pending = False
            else:
                progress = 0
                progress_source = "cycle_reset_hold"
        elif self._progress_reset_pending:
            self._progress_reset_pending = False
        if (
            not progress_was_pending
            and not reset_marked
            and active
            and previous_progress is not None
            and progress is not None
            and progress < previous_progress
        ):
            progress = previous_progress
            progress_source = "last_known_monotonic"
        if progress is None and previous_progress is not None:
            progress = previous_progress
            progress_source = "last_known"
        snapshot["mowing_progress"] = progress
        snapshot["mowing_progress_source"] = progress_source
        snapshot["task_progress_source"] = progress_source
        snapshot["mowing_progress_source_age"] = (
            self._age_since(self._mqtt_progress_last_update)
            if progress_source == "mqtt_task_percentage"
            else self._private_endpoint_age("location")
            if progress_source == "private_task_percentage"
            else None
        )
        snapshot["mowing_progress_mqtt"] = mqtt_progress
        snapshot["mowing_progress_private_cloud"] = private_progress
        snapshot["task_progress_private_cloud"] = private_progress

        def _valid_zone_id(value: Any) -> int | None:
            parsed = _as_int(value)
            return parsed if parsed is not None and parsed > 0 else None

        mqtt_zone_id = _valid_zone_id(
            (self._mqtt_location or {}).get("work_target_zone")
        )
        cloud_zone_id = _valid_zone_id(snapshot.get("work_target_zone"))
        physical_zone_id = _valid_zone_id(
            snapshot.get("current_physical_zone_id")
        )
        # Work/zone progress belongs only to an explicit work target.
        active_progress_zone = mqtt_zone_id or cloud_zone_id
        if active_progress_zone is None and active:
            active_progress_zone = previous_zone_id = _valid_zone_id(
                previous.get("active_zone_progress_zone_id")
            )
        else:
            previous_zone_id = _valid_zone_id(
                previous.get("active_zone_progress_zone_id")
            )
        private_zone_progress = _progress_percent(snapshot.get("work_progress"))
        zone_progress_candidates = (
            [
                ("mqtt_map_work_position", mqtt_progress["work_progress"]),
                ("private_map_work_position", private_zone_progress),
                ("mqtt_route_progress", mqtt_progress["route_progress"]),
            ]
            if active
            else [
                ("private_map_work_position", private_zone_progress),
                ("mqtt_map_work_position", mqtt_progress["work_progress"]),
                ("mqtt_route_progress", mqtt_progress["route_progress"]),
            ]
        )
        active_zone_progress = None
        active_zone_progress_source = None
        for source, value in zone_progress_candidates:
            if value is not None:
                active_zone_progress = value
                active_zone_progress_source = source
                break
        previous_zone_progress = _progress_percent(
            previous.get("active_zone_progress")
        )
        same_zone = (
            active_progress_zone is not None
            and active_progress_zone == previous_zone_id
        )
        if (
            active
            and same_zone
            and not reset_marked
            and not progress_was_pending
            and previous_zone_progress is not None
            and active_zone_progress is not None
            and active_zone_progress < previous_zone_progress
        ):
            active_zone_progress = previous_zone_progress
            active_zone_progress_source = "last_known_zone_monotonic"
        if (
            active_zone_progress is None
            and same_zone
            and previous_zone_progress is not None
        ):
            active_zone_progress = previous_zone_progress
            active_zone_progress_source = "last_known_zone"
        snapshot["active_zone_progress"] = active_zone_progress
        snapshot["active_zone_progress_source"] = active_zone_progress_source
        snapshot["active_zone_progress_zone_id"] = active_progress_zone
        snapshot["active_zone_progress_source_age"] = (
            self._age_since(self._mqtt_progress_last_update)
            if str(active_zone_progress_source or "").startswith("mqtt_")
            else self._private_endpoint_age("location", "index2")
            if active_zone_progress_source == "private_map_work_position"
            else None
        )
        if active and active_progress_zone is not None and active_zone_progress is not None:
            zone_details = [
                dict(item)
                for item in snapshot.get("zone_details") or []
                if isinstance(item, dict)
            ]
            self._apply_active_zone_progress(
                zone_details,
                zone_id=active_progress_zone,
                progress_candidates=[
                    (active_zone_progress_source or "active_zone", active_zone_progress)
                ],
            )
            snapshot["zone_details"] = zone_details

        # Coverage is monotonic inside one cycle. A confirmed reset clears it
        # immediately and ignores the old cloud row until a low value arrives.
        # Coverage is intentionally not globally reset or forced monotonic.
        # It represents the latest per-zone values and can legitimately fall
        # when a new cycle begins in one zone while the other zones retain their
        # latest completed values.  The central zone model performs the weighted
        # map/task calculations without mixing these meanings.
        coverage = dict(raw_coverage) if isinstance(raw_coverage, dict) else None
        if coverage is None and isinstance(previous_coverage, dict):
            coverage = dict(previous_coverage)
        snapshot["coverage"] = coverage
        snapshot["coverage_source"] = "private_cloud" if coverage is not None else None
        self._coverage_reset_pending = False

        # Session area can arrive both in the official location stream and the
        # private cloud. Reject transient zero/regressions unless a cycle reset
        # has been confirmed.
        mqtt_area_age = self._age_since(self._mqtt_area_last_update)
        mqtt_area = (
            _as_float((self._mqtt_location or {}).get("subtotal_area"))
            if mqtt_area_age is not None
            and mqtt_area_age <= MQTT_TELEMETRY_STALE_SECONDS
            else None
        )
        private_area = _as_float(snapshot.get("session_area_private_cloud"))
        area_candidates = (
            [("mqtt_location", mqtt_area), ("private_cloud", private_area)]
            if active
            else [("private_cloud", private_area), ("mqtt_location", mqtt_area)]
        )
        session_area = None
        session_area_source = None
        for source, value in area_candidates:
            if value is not None and value >= 0:
                session_area = value
                session_area_source = source
                break
        previous_area = _as_float(previous.get("session_area"))
        area_was_pending = self._area_reset_pending
        if area_was_pending and reset_guard:
            reset_reference = self._cycle_reset_previous_area or previous_area or 0.0
            acceptable = max(250.0, reset_reference * 0.25)
            low_progress = any(
                value is not None and value <= 25
                for value in (
                    _progress_percent(snapshot.get("mowing_progress")),
                    _progress_percent((snapshot.get("coverage") or {}).get("overall_pct")),
                )
            )
            if session_area is not None and (
                session_area <= acceptable
                or (low_progress and session_area <= max(500.0, reset_reference * 0.5))
            ):
                self._area_reset_pending = False
            else:
                session_area = 0.0
                session_area_source = "cycle_reset_hold"
        elif self._area_reset_pending:
            self._area_reset_pending = False
        if (
            not area_was_pending
            and not reset_marked
            and active
            and previous_area is not None
            and session_area is not None
            and session_area < previous_area
        ):
            session_area = previous_area
            session_area_source = "last_known_monotonic"
        if session_area is None and previous_area is not None:
            session_area = previous_area
            session_area_source = "last_known"
        snapshot["session_area"] = session_area
        snapshot["session_area_source"] = session_area_source
        snapshot["session_area_mqtt"] = mqtt_area
        snapshot["session_area_private_cloud"] = private_area
        snapshot["session_area_source_age"] = (
            mqtt_area_age
            if session_area_source == "mqtt_location"
            else self._private_endpoint_age("location")
            if session_area_source == "private_cloud"
            else None
        )

        private_total = _as_float(snapshot.get("total_area_private_cloud"))
        map_total = _as_float((snapshot.get("map") or {}).get("area"))
        previous_total = _as_float(previous.get("total_area"))
        restored_total = _as_float(self._restored_telemetry.get("total_area"))
        if private_total is not None and private_total > 0:
            total_area = private_total
            total_area_source = "private_cloud"
        elif map_total is not None and map_total > 0:
            total_area = map_total
            total_area_source = "map_cache"
        elif previous_total is not None and previous_total > 0:
            total_area = previous_total
            total_area_source = "last_known"
        elif restored_total is not None and restored_total > 0:
            total_area = restored_total
            total_area_source = "persisted_last_known"
        else:
            total_area = None
            total_area_source = None
        snapshot["total_area"] = total_area
        snapshot["total_area_source"] = total_area_source

        snapshot["cycle_value_reset_pending"] = bool(
            self._progress_reset_pending
            or self._coverage_reset_pending
            or self._area_reset_pending
        )
        snapshot["cycle_value_reset_reason"] = self._cycle_reset_reason
        snapshot["cycle_value_reset_age"] = self._age_since(
            self._cycle_reset_started_mono
        )

    def _schedule_state_save(
        self, snapshot: dict[str, Any] | None = None
    ) -> None:
        cached = self._state_store_data(snapshot)
        self._state_store.async_delay_save(
            lambda: cached, MQTT_HISTORY_SAVE_DELAY_SECONDS
        )

    def _fresh_mqtt_position(self) -> dict[str, float] | None:
        if self._mqtt_location is None or self._mqtt_last_update is None:
            return None
        if time.monotonic() - self._mqtt_last_update > MQTT_POSE_STALE_SECONDS:
            return None
        x, y = _as_float(self._mqtt_location.get("x")), _as_float(self._mqtt_location.get("y"))
        if x is None or y is None:
            return None
        return {"x": x, "y": y, "heading": _as_float(self._mqtt_location.get("theta"))}

    def mqtt_state_age(self) -> float | None:
        if self._mqtt_state_last_update is None:
            return None
        return max(0.0, time.monotonic() - self._mqtt_state_last_update)

    def mqtt_action_age(self) -> float | None:
        if self._mqtt_action_last_update is None:
            return None
        return max(0.0, time.monotonic() - self._mqtt_action_last_update)

    def _fresh_mqtt_vehicle_state(self) -> int | None:
        age = self.mqtt_state_age()
        if age is None or age > MQTT_STATE_STALE_SECONDS:
            return None
        return _as_int((self._mqtt_location or {}).get("vehicle_state"))

    def _fresh_mqtt_action(self) -> int | None:
        age = self.mqtt_action_age()
        if age is None or age > MQTT_STATE_STALE_SECONDS:
            return None
        return _as_int((self._mqtt_location or {}).get("action"))

    def _resolved_docked_state(
        self,
        state_code: str | None,
        mqtt_vehicle_state: int | None,
        activity: str | None,
        pending_activity: str | None,
    ) -> tuple[bool, str]:
        if pending_activity in {ACTIVITY_MOWING, ACTIVITY_PAUSED, ACTIVITY_RETURNING}:
            return False, "pending_activity"
        if mqtt_vehicle_state in {MQTT_STATE_MOWING, MQTT_STATE_RETURNING, MQTT_STATE_MAPPING}:
            return False, "mqtt_active_state"
        if activity in {ACTIVITY_MOWING, ACTIVITY_PAUSED, ACTIVITY_RETURNING}:
            return False, "normalized_activity"
        if mqtt_vehicle_state in MQTT_DOCKED_STATES:
            return True, "mqtt_docked_state"
        if str(state_code or "") in DOCKED_STATES:
            return True, "private_docked_state"
        return False, "not_docked"

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
        """Return docked state, treating MQTT idle as neutral context."""
        if mqtt_vehicle_state in MQTT_DOCKED_STATES:
            return True
        if mqtt_vehicle_state in {MQTT_STATE_MOWING, MQTT_STATE_RETURNING, MQTT_STATE_MAPPING}:
            return False
        if mqtt_vehicle_state == MQTT_STATE_IDLE:
            return str(state_code or "") in DOCKED_STATES
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

    def _pending_activity_value(self) -> str | None:
        """Return a fresh optimistic command activity, clearing expired state."""
        if self._pending_activity is None or self._pending_activity_set_at is None:
            return None
        if (
            time.monotonic() - self._pending_activity_set_at
            > COMMAND_ACTIVITY_TTL_SECONDS
        ):
            self._pending_activity = None
            self._pending_activity_set_at = None
            return None
        return self._pending_activity

    def set_pending_activity(self, activity: str) -> None:
        """Publish an optimistic activity while the mower acknowledges a command."""
        self._pending_activity = str(activity)
        self._pending_activity_set_at = time.monotonic()
        if self.data:
            updated = dict(self.data)
            updated["activity"] = activity
            if activity in {ACTIVITY_MOWING, ACTIVITY_PAUSED, ACTIVITY_RETURNING}:
                updated["docked"] = False
            updated.update(self._navigation_fields(updated))
            self.async_set_updated_data(updated)

    def clear_pending_activity(self) -> None:
        """Clear an optimistic command activity after failure or confirmation."""
        self._pending_activity = None
        self._pending_activity_set_at = None

    def set_command_target(
        self, zone_ids: list[int], *, source: str = "ha_mow_command"
    ) -> None:
        """Latch an explicit ordered-zone command for gate pre-opening."""
        self._command_target_zone_ids = _dedupe_zone_ids(zone_ids)
        self._command_target_set_at = (
            time.monotonic() if self._command_target_zone_ids else None
        )
        self._command_target_source = source if self._command_target_zone_ids else None
        if self.data:
            updated = dict(self.data)
            updated.update(self._navigation_fields(updated))
            self.async_set_updated_data(updated)

    def clear_command_target(self) -> None:
        """Clear locally latched navigation intent."""
        self._command_target_zone_ids = []
        self._command_target_set_at = None
        self._command_target_source = None

    def _mow_command_state_snapshot(self) -> dict[str, Any]:
        """Return the small live-state subset useful for command debugging."""
        data = self.data or {}
        active_session = self.history.active_session
        return {
            "state_code": data.get("state_code"),
            "activity": data.get("activity"),
            "docked": data.get("docked"),
            "mqtt_vehicle_state": data.get("mqtt_vehicle_state"),
            "mqtt_action": data.get("mqtt_action"),
            "current_physical_zone_id": data.get("current_physical_zone_id"),
            "target_zone_ids": list(data.get("target_zone_ids") or []),
            "target_zone_source": data.get("target_zone_source"),
            "mowing_progress": data.get("mowing_progress"),
            "work_progress_raw": data.get("work_progress"),
            "route_progress_raw": data.get("mow_route_progress"),
            "session_area": data.get("session_area"),
            "active_session_id": (active_session or {}).get("id")
            if isinstance(active_session, dict)
            else None,
        }

    def begin_mow_command_trace(
        self,
        *,
        source: str,
        requested_zone_ids: list[int],
        resolved_zone_ids: list[int],
        reset: bool,
        ordered: bool,
        partition_ids_hex: str,
        partition_setup: int,
    ) -> None:
        """Remember the exact last mowing command for a later diagnostics export."""
        requested = _dedupe_zone_ids(requested_zone_ids)
        resolved = _dedupe_zone_ids(resolved_zone_ids)
        known_zones = [
            {"id": _as_int(zone.get("id")), "name": zone.get("name")}
            for zone in (self.data or {}).get("zones") or []
            if isinstance(zone, dict) and _as_int(zone.get("id")) is not None
        ]
        names = {row["id"]: row.get("name") for row in known_zones}
        big_endian_reference = "".join(
            int(zone_id).to_bytes(2, "big", signed=False).hex()
            for zone_id in resolved
        )
        self._last_mow_command_trace = {
            "started_at_utc": datetime.now(UTC).isoformat(),
            "_started_monotonic": time.monotonic(),
            "source": str(source),
            "model": (self.data or {}).get("model")
            or self.entry.data.get("model"),
            "vehicle_type": self.vehicle_type,
            "explicit_zone_selection": bool(requested),
            "requested_zone_ids": requested,
            "requested_zone_names": [names.get(value) for value in requested],
            "resolved_zone_ids": resolved,
            "resolved_zone_names": [names.get(value) for value in resolved],
            "known_zones": known_zones,
            "reset": bool(reset),
            "ordered": bool(ordered),
            "partition_setup": int(partition_setup),
            "partition_setup_hex": f"0x{int(partition_setup):02X}",
            "partition_ids_hex_sent": str(partition_ids_hex).upper(),
            "partition_ids_big_endian_reference": big_endian_reference.upper(),
            "request_shape": {
                "cmdCode": "s:mower",
                "data": {
                    "partitionSetup": int(partition_setup),
                    "partitionIds": str(partition_ids_hex),
                },
            },
            "send_response": None,
            "cmd_num": None,
            "send_error": None,
            "state_before": self._mow_command_state_snapshot(),
        }

    def record_mow_command_result(self, result: Any) -> None:
        """Attach the private-cloud acknowledgement to the active command trace."""
        if self._last_mow_command_trace is None:
            return
        self._last_mow_command_trace["send_completed_at_utc"] = (
            datetime.now(UTC).isoformat()
        )
        self._last_mow_command_trace["send_response"] = deepcopy(result)
        self._last_mow_command_trace["cmd_num"] = _extract_command_number(result)
        self._last_mow_command_trace["state_after_send"] = (
            self._mow_command_state_snapshot()
        )

    def record_mow_command_error(self, error: BaseException) -> None:
        """Preserve a failed send attempt instead of losing its payload details."""
        if self._last_mow_command_trace is None:
            return
        self._last_mow_command_trace["send_completed_at_utc"] = (
            datetime.now(UTC).isoformat()
        )
        self._last_mow_command_trace["send_error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }
        self._last_mow_command_trace["state_after_send"] = (
            self._mow_command_state_snapshot()
        )

    def mow_command_diagnostics(self) -> dict[str, Any] | None:
        """Return a sanitized-ready copy of the last user-issued mowing command."""
        if self._last_mow_command_trace is None:
            return None
        result = deepcopy(self._last_mow_command_trace)
        started = _as_float(result.pop("_started_monotonic", None))
        result["age_s"] = (
            round(max(0.0, time.monotonic() - started), 3)
            if started is not None
            else None
        )
        result["state_at_diagnostics"] = self._mow_command_state_snapshot()
        return result

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
        """Derive physical zone, target zone, channel and gate intent from live X/Y."""
        position = self._fresh_mqtt_position()
        pose_valid = position is not None
        map_data = snapshot.get("map") or {}
        zones = map_data.get("zones") or snapshot.get("zones") or []
        channels = map_data.get("channels") or []
        zone_names: dict[int, str] = {}
        for zone in zones:
            if not isinstance(zone, dict):
                continue
            zone_id = _as_int(zone.get("id"))
            if zone_id is not None:
                zone_names[zone_id] = str(zone.get("name") or f"Zone {zone_id}")

        physical = _zone_at_position(position, zones) if pose_valid else None
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
            tunnel = _tunnel_at_position(position, channels)

        station = map_data.get("station") or {}
        dock_zone = (
            _zone_at_position(
                {"x": station.get("x"), "y": station.get("y")}, zones
            )
            if station
            else None
        )
        dock_zone_id = _as_int((dock_zone or {}).get("id"))

        physical_id = _as_int((physical or {}).get("id"))
        physical_name = (physical or {}).get("name")
        if physical_id is not None and physical_name:
            self._last_physical_zone_id = physical_id
            self._last_physical_zone_name = str(physical_name)

        state_code = str(snapshot.get("state_code") or "")
        mqtt_state = self._fresh_mqtt_vehicle_state()
        pending_activity = self._pending_activity_value()
        raw_docked = bool(snapshot.get("docked"))
        raw_returning = self._is_returning_state(state_code, mqtt_state)
        command_activity_active = pending_activity in {
            ACTIVITY_MOWING,
            ACTIVITY_PAUSED,
            ACTIVITY_RETURNING,
        }
        docked_confirmed = bool(
            raw_docked
            and not command_activity_active
            and (
                mqtt_state in MQTT_DOCKED_STATES
                or (dock_zone_id is not None and physical_id == dock_zone_id)
                or (
                    not pose_valid
                    and not self._gate_latches
                    and not self._command_target_zone_ids
                )
            )
        )
        is_returning = raw_returning or pending_activity == ACTIVITY_RETURNING

        mqtt_work_target = _as_int(
            (self._mqtt_location or {}).get("work_target_zone")
        )
        cloud_work_target = _as_int(snapshot.get("work_target_zone"))
        mqtt_partition_ids = _dedupe_zone_ids(
            (self._mqtt_location or {}).get("partition_ids")
        )
        cloud_zone_ids = _dedupe_zone_ids(snapshot.get("current_zone_ids"))
        now_monotonic = time.monotonic()
        command_fresh = bool(
            self._command_target_zone_ids
            and _command_target_is_fresh(
                self._command_target_set_at, now_monotonic
            )
        )
        if self._command_target_zone_ids and not command_fresh:
            self.clear_command_target()
        command_target_age = (
            round(now_monotonic - self._command_target_set_at, 1)
            if command_fresh and self._command_target_set_at is not None
            else None
        )
        command_source = self._command_target_source if command_fresh else None

        target_ids, target_source, command_confirmed = _resolve_navigation_target_ids(
            is_docked=docked_confirmed,
            is_returning=is_returning,
            dock_zone_id=dock_zone_id,
            physical_zone_id=physical_id,
            command_target_ids=self._command_target_zone_ids,
            command_target_fresh=command_fresh,
            mqtt_work_target=mqtt_work_target,
            cloud_work_target=cloud_work_target,
            mqtt_partition_ids=mqtt_partition_ids,
            cloud_zone_ids=cloud_zone_ids,
            last_target_ids=self._last_target_zone_ids,
        )

        self._gate_arrival_guards = {
            slug: guard
            for slug, guard in self._gate_arrival_guards.items()
            if _as_float(guard.get("arrived_at")) is not None
            and 0 <= now_monotonic - float(guard["arrived_at"])
            <= GATE_ARRIVAL_GUARD_SECONDS
        }
        target_ids, target_source, held_arrival_guard = _apply_gate_arrival_guard(
            target_ids=target_ids,
            target_source=target_source,
            physical_zone_id=physical_id,
            guards=self._gate_arrival_guards,
            now_monotonic=now_monotonic,
            command_fresh=command_fresh,
            is_returning=is_returning,
        )

        if command_confirmed:
            # Use the confirmed command for this snapshot, then let subsequent
            # packets follow the mower's own immediate target.
            self.clear_command_target()

        if docked_confirmed:
            self.clear_command_target()
            self._last_target_zone_ids = []
            self._gate_latches.clear()
            self._gate_arrival_guards.clear()
            for slug in list(self._gate_release_tasks):
                self._cancel_gate_release(slug)
        elif target_ids:
            self._last_target_zone_ids = list(target_ids)

        target_names = [
            zone_names.get(zone_id, f"Zone {zone_id}") for zone_id in target_ids
        ]
        tunnel_connection = _dedupe_zone_ids((tunnel or {}).get("connection"))

        if pose_valid:
            if physical_name:
                physical_state = str(physical_name)
                physical_source = (physical or {}).get("source", "map_polygon")
            elif tunnel is not None:
                physical_state = "Between zones"
                physical_source = "mapped_channel"
            else:
                physical_state = "Outside mapped zones"
                physical_source = "live_pose"
        elif docked_confirmed and dock_zone_id is not None:
            physical_state = zone_names.get(dock_zone_id, f"Zone {dock_zone_id}")
            physical_source = "confirmed_dock_zone"
        elif self._last_physical_zone_name:
            physical_state = self._last_physical_zone_name
            physical_source = "last_known_stale_pose"
        else:
            physical_state = "Position unavailable"
            physical_source = "pose_unavailable"

        target_state = ", ".join(target_names) if target_names else "No active target"
        live_tunnel_id = _as_int((tunnel or {}).get("id"))
        live_tunnel_distance = _as_float((tunnel or {}).get("distance"))
        if pose_valid:
            if tunnel is not None:
                tunnel_state = str(
                    tunnel.get("name")
                    or (
                        f"Channel {tunnel.get('id')}"
                        if tunnel.get("id") is not None
                        else "Channel"
                    )
                )
            else:
                tunnel_state = "Not in channel"
            tunnel_source = "live_pose"
            tunnel_stale = False
            self._last_channel_state = tunnel_state
            self._last_channel_id = live_tunnel_id
            self._last_channel_connection = list(tunnel_connection)
            self._last_channel_distance = live_tunnel_distance
        elif docked_confirmed:
            # A confirmed dock is physically outside a mapped transit Channel.
            # Do not alternate to Position unavailable when the idle pose ages.
            tunnel_state = "Not in channel"
            tunnel_source = "confirmed_docked"
            tunnel_stale = False
            live_tunnel_id = None
            tunnel_connection = []
            live_tunnel_distance = None
            self._last_channel_state = tunnel_state
            self._last_channel_id = None
            self._last_channel_connection = []
            self._last_channel_distance = None
        elif self._last_channel_state is not None:
            tunnel_state = self._last_channel_state
            tunnel_source = "last_known_stale_pose"
            tunnel_stale = True
            live_tunnel_id = self._last_channel_id
            tunnel_connection = list(self._last_channel_connection)
            live_tunnel_distance = self._last_channel_distance
        else:
            tunnel_state = "Position unavailable"
            tunnel_source = "pose_unavailable"
            tunnel_stale = True

        if not pose_valid:
            transition: bool | None = False if docked_confirmed else None
        elif len(target_ids) == 1:
            target_id = target_ids[0]
            transition = bool(
                (physical_id is not None and physical_id != target_id)
                or (tunnel is not None and target_id in tunnel_connection)
            )
        else:
            transition = False

        gate_states: dict[str, dict[str, Any]] = {}
        for gate in self.gates:
            pair = set(gate.zones)
            target_id = target_ids[0] if len(target_ids) == 1 else None
            intent = gate.allows_transition(physical_id, target_id)
            latch = self._gate_latches.get(gate.slug)
            tunnel_from_id = gate.other_zone(target_id) if target_id is not None else None
            tunnel_direction_allowed = bool(
                target_id is not None
                and tunnel_from_id is not None
                and gate.allows_transition(tunnel_from_id, target_id)
            )
            tunnel_matches = bool(
                tunnel is not None
                and set(tunnel_connection) == pair
                and (latch is not None or tunnel_direction_allowed)
            )

            # Do not overwrite an in-flight latch when a stale target briefly
            # flips to the origin/other side. The original from->to direction is
            # authoritative until arrival and close-delay release.
            if intent and latch is None:
                self._cancel_gate_release(gate.slug)
                latch = {
                    "from_zone_id": int(physical_id),
                    "to_zone_id": int(target_id),
                    "release_at": None,
                    "target_source": target_source,
                }
                self._gate_latches[gate.slug] = latch

            if latch:
                from_id = _as_int(latch.get("from_zone_id"))
                to_id = _as_int(latch.get("to_zone_id"))
                arrived = bool(
                    pose_valid
                    and tunnel is None
                    and to_id is not None
                    and physical_id == to_id
                )
                if arrived:
                    if gate.slug not in self._gate_arrival_guards:
                        self._gate_arrival_guards[gate.slug] = {
                            "from_zone_id": from_id,
                            "to_zone_id": to_id,
                            "arrived_at": now_monotonic,
                            "target_source": (latch or {}).get("target_source"),
                        }
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
                    if not gate.allows_transition(from_id, to_id):
                        self._gate_latches.pop(gate.slug, None)
                        self._cancel_gate_release(gate.slug)
                        latch = None

            if not pose_valid:
                if latch:
                    required: bool | None = True
                elif docked_confirmed:
                    required = False
                else:
                    required = None
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
                "target_source": (latch or {}).get("target_source") or target_source,
                "command_source": command_source,
                "target_age_seconds": command_target_age,
                "command_target_active": command_fresh,
                "intent_confirmed": command_confirmed,
                "current_channel_id": _as_int((tunnel or {}).get("id")),
                "current_channel_name": tunnel_state,
                "pose_age": self.pose_age(),
                "pose_valid": pose_valid,
                "arrival_guard_active": bool(
                    (
                        held_arrival_guard
                        and held_arrival_guard.get("slug") == gate.slug
                    )
                    or gate.slug in self._gate_arrival_guards
                ),
                "arrival_guard_age_seconds": (
                    held_arrival_guard.get("age_seconds")
                    if (
                        held_arrival_guard
                        and held_arrival_guard.get("slug") == gate.slug
                    )
                    else (
                        round(
                            now_monotonic
                            - float(self._gate_arrival_guards[gate.slug]["arrived_at"]),
                            1,
                        )
                        if gate.slug in self._gate_arrival_guards
                        else None
                    )
                ),
            }

        if transition is False and any(
            state.get("required") is True for state in gate_states.values()
        ):
            transition = True

        return {
            "current_physical_zone": physical_state,
            "current_physical_zone_id": physical_id,
            "current_physical_zone_source": physical_source,
            "target_zone": target_state,
            "target_zone_ids": target_ids,
            "target_zone_source": target_source,
            "target_zone_command_source": command_source,
            "target_zone_age_seconds": command_target_age,
            "command_target_active": command_fresh,
            "current_channel": tunnel_state,
            "current_channel_id": live_tunnel_id,
            "current_channel_connection": tunnel_connection,
            "current_channel_distance": live_tunnel_distance,
            "current_channel_source": tunnel_source,
            "current_channel_stale": tunnel_stale,
            "current_channel_pose_valid": pose_valid,
            "current_channel_pose_age": self.pose_age(),
            "dock_zone_id": dock_zone_id,
            "zone_transition": transition,
            "gate_states": gate_states,
            "gate_arrival_guards": {
                slug: {
                    **dict(guard),
                    "age_seconds": round(
                        now_monotonic - float(guard.get("arrived_at") or now_monotonic),
                        1,
                    ),
                }
                for slug, guard in self._gate_arrival_guards.items()
            },
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
        """Age in seconds of the latest true MQTT pose packet."""
        if self._mqtt_last_update is None:
            return None
        return max(0.0, time.monotonic() - self._mqtt_last_update)

    def mqtt_message_age(self) -> float | None:
        """Age of the latest relevant message on the MQTT location topic."""
        if self._mqtt_last_message_update is None:
            return None
        return max(0.0, time.monotonic() - self._mqtt_last_message_update)

    def mqtt_stream_expected(self) -> bool:
        """Return whether the mower should currently emit live location data."""
        data = self.data or {}
        activity = data.get("activity")
        state_code = str(data.get("state_code") or "")
        mqtt_state = self._fresh_mqtt_vehicle_state()
        mqtt_active = mqtt_state in {
            MQTT_STATE_MOWING,
            MQTT_STATE_RETURNING,
            MQTT_STATE_MAPPING,
        }
        if self._fresh_mqtt_position() is not None and mqtt_active:
            return True
        if activity in {ACTIVITY_MOWING, ACTIVITY_RETURNING} or state_code in ACTIVE_STATES:
            return True
        if activity in {ACTIVITY_DOCKED, ACTIVITY_ERROR} or state_code in DOCKED_STATES:
            return False
        message_age = self.mqtt_message_age()
        return bool(mqtt_active and message_age is not None and message_age <= 45)

    def request_fast_refresh(self, reason: str) -> None:
        """Prompt one throttled private refresh after an important MQTT event."""
        if self._shutdown_complete:
            return
        now = time.monotonic()
        if now - self._last_fast_refresh_request_mono < PRIVATE_FAST_REFRESH_MIN_SECONDS:
            return
        self._last_fast_refresh_request_mono = now
        self.update_interval = timedelta(seconds=MOW_SCAN_INTERVAL)

        async def _refresh() -> None:
            try:
                await self.async_request_refresh()
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Fast private refresh after %s failed", reason, exc_info=True
                )

        self.hass.async_create_task(
            _refresh(),
            f"Navimower fast private refresh {self.entry.entry_id}",
        )

    def publish_connectivity(self) -> None:
        """Publish bridge/poll health changes without touching entity values."""
        if self.data:
            snapshot = dict(self.data)
            snapshot.update(self._connectivity_fields())
            self.async_set_updated_data(snapshot)

    def _connectivity_fields(self) -> dict[str, Any]:
        bridge = getattr(self, "mqtt_bridge", None)
        mqtt_health = (
            bridge.diagnostic_health()
            if bridge is not None and hasattr(bridge, "diagnostic_health")
            else {}
        )
        poll_age = self.private_poll_age()
        core_age = self.private_core_age()
        return {
            "private_cloud_connected": self._private_cloud_connected,
            "private_cloud_error": self._last_private_error,
            "private_poll_age": poll_age,
            "private_core_age": core_age,
            "private_poll_profile": (
                "active" if self._private_poll_active() else "idle"
            ),
            "oauth_configured": bool(self.entry.data.get(CONF_OAUTH_TOKEN)),
            "oauth_connected": self._oauth_connected,
            "oauth_error": self._last_oauth_error,
            "mqtt_configured": self._mqtt_configured,
            "mqtt_connected": self._mqtt_connected,
            "mqtt_error": self._last_mqtt_error,
            "mqtt_pose_age": self.pose_age(),
            "mqtt_pose_valid": self._fresh_mqtt_position() is not None,
            "mqtt_state_age": self.mqtt_state_age(),
            "mqtt_action_age": self.mqtt_action_age(),
            "mqtt_stream_state": mqtt_health.get("stream_state"),
            "mqtt_stream_expected": mqtt_health.get("stream_expected"),
            "mqtt_recovery_count": mqtt_health.get("recovery_count", 0),
            "mqtt_last_recovery_reason": mqtt_health.get("last_recovery_reason"),
            "mqtt_last_location_message_age": mqtt_health.get(
                "last_location_message_age_s"
            ),
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
        previous_snapshot = dict(self.data or {})
        position = self._fresh_mqtt_position()
        mqtt_state = self._fresh_mqtt_vehicle_state()
        mqtt_action = self._fresh_mqtt_action()
        if position is not None:
            snapshot["position"] = position
            snapshot["pose_source"] = "mqtt"
            snapshot["pose_time"] = (self._mqtt_location or {}).get("pose_time")
        snapshot["mqtt_vehicle_state"] = mqtt_state
        snapshot["mqtt_action"] = mqtt_action
        snapshot["mqtt_state_age"] = self.mqtt_state_age()
        snapshot["mqtt_action_age"] = self.mqtt_action_age()
        cutting = self._is_cutting(snapshot.get("state_code"), mqtt_state, mqtt_action)
        snapshot["trail_active"] = cutting
        activity = snapshot.get("activity")
        if cutting:
            activity = ACTIVITY_MOWING
        elif mqtt_state == MQTT_STATE_RETURNING:
            activity = ACTIVITY_RETURNING
        elif mqtt_state in MQTT_DOCKED_STATES:
            activity = ACTIVITY_DOCKED
        pending_activity = self._pending_activity_value()
        if pending_activity is not None:
            activity = pending_activity
        snapshot["activity"] = activity
        docked, docked_source = self._resolved_docked_state(
            str(snapshot.get("state_code") or ""), mqtt_state,
            str(activity or ""), pending_activity,
        )
        snapshot["docked"] = docked
        snapshot["docked_source"] = docked_source
        self._last_docked_source = docked_source
        snapshot.update(self._connectivity_fields())
        snapshot.update(self._navigation_fields(snapshot))
        # MQTT ingestion owns every live route point. A private refresh may
        # still start/finish/update the session while MQTT is fresh, but only
        # contributes a lower-frequency point when no fresh MQTT pose exists.
        history_position = None if position is not None else snapshot.get("position")
        self._update_history(snapshot, history_position, mqtt_state, mqtt_action)
        self._stabilize_telemetry(snapshot, previous_snapshot)
        self.history.update_from_snapshot(snapshot)
        snapshot["zone_details"] = self._merge_zone_history(
            snapshot.get("zone_details") or []
        )
        self._refresh_zone_model(snapshot)
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
        """Merge one official MQTT update and persist the live session path."""
        if not isinstance(location, dict):
            return
        previous_snapshot = dict(self.data or {})
        previous_activity = (self.data or {}).get("activity")
        previous_pose_valid = self._fresh_mqtt_position() is not None
        now_monotonic = time.monotonic()
        self._mqtt_last_message_update = now_monotonic

        merged = dict(self._mqtt_location or {})
        merged.update(location)
        self._mqtt_location = merged

        pose_updated = bool(location.get("_pose_updated")) or (
            self._mqtt_last_update is None
            and _as_float(location.get("x")) is not None
            and _as_float(location.get("y")) is not None
        )
        if pose_updated:
            self._mqtt_last_update = now_monotonic
        if location.get("vehicle_state") is not None:
            self._mqtt_state_last_update = now_monotonic
        if location.get("action") is not None:
            self._mqtt_action_last_update = now_monotonic
        if bool(location.get("_battery_updated")) or (
            self._mqtt_battery_last_update is None
            and location.get("battery") is not None
        ):
            battery = _as_int(location.get("battery"))
            if battery is not None and 0 <= battery <= 100:
                self._mqtt_battery = battery
                self._mqtt_battery_last_update = now_monotonic
        if bool(location.get("_progress_updated")) or (
            self._mqtt_progress_last_update is None
            and any(
                location.get(key) is not None
                for key in ("mow_progress", "work_progress", "mowing_percentage")
            )
        ):
            self._mqtt_progress_last_update = now_monotonic
        if bool(location.get("_area_updated")) or (
            self._mqtt_area_last_update is None
            and any(
                location.get(key) is not None
                for key in ("subtotal_area", "mowing_week_area")
            )
        ):
            self._mqtt_area_last_update = now_monotonic
        self._mqtt_connected = True

        position = self._fresh_mqtt_position()
        mqtt_state = self._fresh_mqtt_vehicle_state()
        mqtt_action = self._fresh_mqtt_action()
        snapshot = dict(self.data or self._bootstrap_snapshot())
        state_code = str(snapshot.get("state_code") or "")
        if position is not None:
            snapshot["position"] = position
            snapshot["pose_source"] = "mqtt"
            snapshot["pose_time"] = merged.get("pose_time")
        snapshot["mqtt_location"] = dict(merged)
        snapshot["mqtt_vehicle_state"] = mqtt_state
        snapshot["mqtt_action"] = mqtt_action
        snapshot["mqtt_state_age"] = self.mqtt_state_age()
        snapshot["mqtt_action_age"] = self.mqtt_action_age()

        cutting = self._is_cutting(state_code, mqtt_state, mqtt_action)
        snapshot["trail_active"] = cutting
        candidate_activity = snapshot.get("activity")
        if cutting:
            candidate_activity = ACTIVITY_MOWING
        elif mqtt_state == MQTT_STATE_RETURNING:
            candidate_activity = ACTIVITY_RETURNING
        elif mqtt_state in MQTT_DOCKED_STATES:
            candidate_activity = ACTIVITY_DOCKED
        pending_activity = self._pending_activity_value()
        if pending_activity is not None:
            if candidate_activity == pending_activity:
                self.clear_pending_activity()
                pending_activity = None
            else:
                candidate_activity = pending_activity
        snapshot["activity"] = candidate_activity
        docked, docked_source = self._resolved_docked_state(
            state_code, mqtt_state, str(candidate_activity or ""), pending_activity
        )
        snapshot["docked"] = docked
        snapshot["docked_source"] = docked_source
        self._last_docked_source = docked_source

        route_progress = _progress_percent(merged.get("mow_progress"))
        work_zone_progress = _progress_percent(merged.get("work_progress"))
        snapshot["mow_route_progress"] = (
            route_progress if route_progress is not None
            else snapshot.get("mow_route_progress")
        )
        snapshot.update(self._connectivity_fields())
        snapshot.update(self._navigation_fields(snapshot))
        if cutting:
            active_zone_id = _as_int(merged.get("work_target_zone"))
            if active_zone_id is None or active_zone_id <= 0:
                active_zone_id = _as_int(merged.get("mow_boundary"))
            if active_zone_id is None or active_zone_id <= 0:
                active_zone_id = _as_int(snapshot.get("current_physical_zone_id"))
            progress_candidates = [
                ("mqtt_map_work_position", work_zone_progress),
                ("mqtt_route_progress", route_progress),
            ]
            active_progress = next(
                (value for _source, value in progress_candidates if value is not None),
                None,
            )
            active_source = next(
                (source for source, value in progress_candidates if value is not None),
                None,
            )
            snapshot["active_zone_progress"] = active_progress
            snapshot["active_zone_progress_source"] = active_source
            snapshot["active_zone_progress_zone_id"] = active_zone_id
            if active_zone_id is not None and active_progress is not None:
                zone_details = [
                    dict(item) for item in snapshot.get("zone_details") or []
                    if isinstance(item, dict)
                ]
                self._apply_active_zone_progress(
                    zone_details,
                    zone_id=active_zone_id,
                    progress_candidates=[(active_source or "active_zone", active_progress)],
                )
                snapshot["zone_details"] = zone_details

        self._update_history(
            snapshot, position if pose_updated else None, mqtt_state, mqtt_action
        )
        self._stabilize_telemetry(snapshot, previous_snapshot)
        self.history.update_from_snapshot(snapshot)
        snapshot["zone_details"] = self._merge_zone_history(
            snapshot.get("zone_details") or []
        )
        self._refresh_zone_model(snapshot)
        snapshot["trail"] = self.history.active_points_xy()
        snapshot["trail_session"] = self.history.active_session_no
        snapshot["trail_started_at"] = self.history.active_started_at()
        snapshot["sessions"] = self.history.session_summaries(include_points=False)
        self._schedule_state_save(snapshot)
        self.async_set_updated_data(snapshot)
        new_activity = snapshot.get("activity")
        if new_activity != previous_activity:
            self.request_fast_refresh(
                f"MQTT activity changed from {previous_activity} to {new_activity}"
            )
        elif pose_updated and not previous_pose_valid:
            self.request_fast_refresh("MQTT pose stream became live")

    def ingest_mqtt_state(self, state: dict[str, Any]) -> None:
        """Merge the official MQTT state packet used for dense battery data."""
        if not isinstance(state, dict):
            return
        battery = _as_int(state.get("battery"))
        if battery is None or not 0 <= battery <= 100:
            return
        previous_snapshot = dict(self.data or {})
        self._mqtt_battery = battery
        self._mqtt_battery_last_update = time.monotonic()
        self._mqtt_connected = True
        snapshot = dict(self.data or self._bootstrap_snapshot())
        self._stabilize_telemetry(snapshot, previous_snapshot)
        snapshot.update(self._connectivity_fields())
        self._schedule_state_save(snapshot)
        self.async_set_updated_data(snapshot)

    def start_new_mowing_cycle(
        self,
        zone_ids: list[int] | None = None,
        *,
        source: str,
    ) -> bool:
        """Split history immediately after a successful reset/new-job command."""
        changed = self.history.start_new_cycle(
            pose_time=int(time.time() * 1000),
            zone_ids=_dedupe_zone_ids(zone_ids or []),
            reason=source,
        )
        if self.data:
            previous_snapshot = dict(self.data)
            self._mark_display_cycle_reset(source, previous_snapshot)
            snapshot = dict(self.data)
            snapshot["cycle_reset_detected"] = changed
            snapshot["cycle_reset_reason"] = source
            snapshot["trail"] = self.history.active_points_xy()
            snapshot["trail_session"] = self.history.active_session_no
            snapshot["trail_started_at"] = self.history.active_started_at()
            snapshot["sessions"] = self.history.session_summaries(include_points=False)
            self._stabilize_telemetry(snapshot, previous_snapshot)
            self.history.update_from_snapshot(snapshot)
            snapshot["zone_details"] = self._merge_zone_history(
                snapshot.get("zone_details") or []
            )
            self._refresh_zone_model(snapshot)
            self._schedule_state_save(snapshot)
            self.async_set_updated_data(snapshot)
        return changed

    def channel_state(self, channel: NavimowerChannel) -> bool | None:
        """Return channel membership, using a safe OFF while confirmed docked."""
        position = self._fresh_mqtt_position()
        if position is None:
            data = self.data or {}
            if data.get("docked") is True and self._pending_activity_value() is None:
                return False
            return None
        return channel.contains(position.get("x"), position.get("y"))

    def _map_payload_with_sessions(
        self, sessions: list[dict[str, Any]], daily_trails: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build the versioned payload consumed by Navimower Map Card."""
        data = self.data or self._bootstrap_snapshot()
        entry_id = self.entry.entry_id
        map_data = data.get("map") or self._map_snapshot(
            self._map_geometry or {},
            cutting_height_supported=data.get("cutting_height_supported"),
        )
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
                    "visited_zone_ids",
                    "task_zone_progress",
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
            "zone_states": data.get("zone_states") or [],
            "zone_states_revision": data.get("zone_states_revision", 0),
            "totals": data.get("totals") or {},
            "daily_trails": daily_trails or {
                "date": dt_util.now().date().isoformat(),
                "revision": self.history.trail_revision,
                "zones": [],
            },
            "daily_trails_revision": (daily_trails or {}).get(
                "revision", self.history.trail_revision
            ),
            "cut_height": (data.get("settings") or {}).get("cut_height"),
            "cutting_height_mm": (data.get("settings") or {}).get("cut_height"),
            "cutting_height_supported": bool(data.get("cutting_height_supported")),
            "schedule_enabled": (data.get("settings") or {}).get("schedule_enabled"),
            "doodles": (map_data or {}).get("doodles") or [],
            # Flat trail is retained for older cards; trail_segments is the
            # gap-aware representation used by map-card v0.1.10 and later.
            "trail": self.history.active_trail_xy(),
            "trail_segments": self.history.active_trail_segments_xy(),
            "trail_session": self.history.active_session_no,
            "trail_started_at": self.history.active_started_at(),
            "trail_active": bool(data.get("trail_active")),
            "active_session": active_meta,
            "current_cycle_session_id": (active_meta or {}).get("id"),
            "history_day_count": 3,
            "completion_threshold_pct": VENDOR_COMPLETION_PROGRESS_MIN,
            "sessions": sessions,
            "session_xy_point_format": list(SESSION_CARD_POINT_FORMAT),
            "session_segment_point_format": list(SESSION_CARD_POINT_FORMAT),
            "session_detail_point_format": list(SESSION_DETAIL_POINT_FORMAT),
            "sessions_api_path": f"/api/navimower/sessions/{entry_id}",
            "session_api_path_template": (
                f"/api/navimower/session/{entry_id}/{{session_id}}"
            ),
            "activity": data.get("activity"),
            "current_physical_zone": data.get("current_physical_zone"),
            "target_zone": data.get("target_zone"),
            "current_channel": data.get("current_channel"),
            "gate_areas": [channel.as_dict() for channel in self.channels],
            "gates": [gate.as_dict() for gate in self.gates],
        }

    def map_payload(self) -> dict[str, Any]:
        """Return a synchronous payload using currently cached session data."""
        return self._map_payload_with_sessions(
            self.history.session_summaries(include_points=True)
        )

    async def async_map_payload(self) -> dict[str, Any]:
        """Return map/card data already prepared by the integration."""
        sessions = await self.history.async_card_sessions()
        map_data = (self.data or {}).get("map") or self._map_snapshot(
            self._map_geometry or {},
            cutting_height_supported=(self.data or {}).get("cutting_height_supported"),
        )
        today = dt_util.now().date().isoformat()
        daily_cache_key = (
            today,
            self.history.trail_revision,
            self._map_cache_key,
        )
        if (
            self._daily_trails_cache_key == daily_cache_key
            and self._daily_trails_cache is not None
        ):
            daily_trails = self._daily_trails_cache
        else:
            daily_trails = await self.history.async_daily_zone_trails(
                [
                    dict(item)
                    for item in (map_data or {}).get("zones") or []
                    if isinstance(item, dict)
                ]
            )
            self._daily_trails_cache_key = daily_cache_key
            self._daily_trails_cache = daily_trails
        return self._map_payload_with_sessions(sessions, daily_trails)

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
