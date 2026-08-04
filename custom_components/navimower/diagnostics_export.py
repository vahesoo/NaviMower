"""Sanitized private-cloud and MQTT diagnostics export for Navimower.

This module intentionally performs read-only requests. It never changes mower
settings, edits a map, or sends a motion command. The existing private-cloud client
may refresh/re-establish its session if the stored session has expired, exactly as it
does during a normal coordinator refresh.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_DIAGNOSTICS_DETAIL,
    MAP_API_SCHEMA_VERSION,
    OPT_DIAGNOSTICS_DETAIL,
)
from .history import SESSION_DETAIL_POINT_FORMAT
from .map_identifiers import resolve_map_identifiers


_KEYWORDS = (
    "angle",
    "battery",
    "charge",
    "boundary",
    "camera",
    "cbox",
    "clock",
    "direction",
    "doodle",
    "edge",
    "firmware",
    "height",
    "image",
    "img",
    "laser",
    "lidar",
    "map",
    "mow",
    "network",
    "oauth",
    "path",
    "resource",
    "rtk",
    "signal",
    "scene",
    "session",
    "sha",
    "snapshot",
    "terrain",
    "trail",
    "url",
    "vision",
)

_EXACT_SENSITIVE_KEYS = {
    "access_token",
    "refresh_token",
    "token",
    "password",
    "pwd",
    "pwdinfo",
    "secret",
    "authorization",
    "cookie",
    "email",
    "phone",
    "uid",
    "uuid",
    "auth_uid",
    "user_id",
    "userid",
    "username",
    "user_name",
    "vehicle_sn",
    "serial",
    "serial_number",
    "sn",
    "device_id",
    "oauth_device_id",
    "client_id",
    "ssid",
    "bssid",
    "mac",
    "ip",
    "ip_address",
    "iccid",
    "pin_code",
    "pincode",
    "rtk",
    "anchor",
    "anti_theft_point",
    "antitheftpoint",
    "latitude",
    "longitude",
    "last_latitude",
    "last_longitude",
    "origin_gps",
    "center_gps",
    "ne_gps",
    "sw_gps",
}


def _is_sensitive_key(key: str) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    if normalized in _EXACT_SENSITIVE_KEYS:
        return True
    if "token" in normalized or "password" in normalized or "secret" in normalized:
        return True
    if normalized.endswith("_uid") or normalized.endswith("_uuid"):
        return True
    if "latitude" in normalized or "longitude" in normalized:
        return True
    if normalized.endswith("_gps") or normalized.startswith("gps_"):
        return True
    if normalized.endswith("_ssid") or normalized.endswith("_bssid"):
        return True
    return False


def _safe_url(value: str) -> str:
    """Keep a URL's location but remove credentials, query and fragment."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<redacted-url>"
    if not parsed.scheme or not parsed.netloc:
        return "<redacted-url>"
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme, f"{host}{port}", parsed.path, "", ""))


def _large_value_summary(value: str) -> dict[str, Any]:
    raw = value.encode("utf-8", errors="replace")
    return {
        "_omitted": "large_string",
        "length": len(value),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


_RTK_SAFE_KEYWORDS = (
    "satellite",
    "satellites",
    "sat_count",
    "satellite_count",
    "fix",
    "quality",
    "hdop",
    "pdop",
    "vdop",
    "snr",
    "accuracy",
    "status",
    "state",
    "mode",
    "source",
    "solution",
    "age",
    "ratio",
)

_RTK_BLOCKED_KEYWORDS = (
    "latitude",
    "longitude",
    "lat",
    "lon",
    "gps",
    "coordinate",
    "position",
    "point",
    "anchor",
    "base",
    "origin",
    "northing",
    "easting",
    "altitude",
)


def _rtk_value_bytes(value: Any) -> bytes:
    """Return stable bytes for RTK metadata hashing without exposing its value."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace")
    try:
        rendered = json.dumps(
            value, ensure_ascii=False, sort_keys=True, default=repr
        )
    except (TypeError, ValueError):
        rendered = repr(value)
    return rendered.encode("utf-8", errors="replace")


def _safe_rtk_fields(value: Any) -> dict[str, Any]:
    """Extract only non-location RTK quality/status scalar fields."""
    found: dict[str, Any] = {}

    def walk(current: Any, path: str = "") -> None:
        if isinstance(current, Mapping):
            for raw_key, child in current.items():
                key = str(raw_key)
                child_path = f"{path}.{key}" if path else key
                normalized = key.strip().lower().replace("-", "_")
                blocked = any(token in normalized for token in _RTK_BLOCKED_KEYWORDS)
                allowed = any(token in normalized for token in _RTK_SAFE_KEYWORDS)
                if (
                    allowed
                    and not blocked
                    and (child is None or isinstance(child, (bool, int, float, str)))
                ):
                    found[child_path] = child
                elif isinstance(child, (Mapping, list, tuple)):
                    walk(child, child_path)
            return

        if isinstance(current, (list, tuple)):
            for index, child in enumerate(current[:25]):
                child_path = f"{path}[{index}]" if path else f"[{index}]"
                if isinstance(child, (Mapping, list, tuple)):
                    walk(child, child_path)

    walk(value)
    return found


def rtk_diagnostics(location: Any) -> dict[str, Any]:
    """Describe the RTK payload while keeping coordinates and anchors private."""
    if not isinstance(location, Mapping) or "rtk" not in location:
        return {"present": False}

    raw = location.get("rtk")
    encoded = _rtk_value_bytes(raw)
    result: dict[str, Any] = {
        "present": raw is not None,
        "raw_type": _value_type(raw),
        "raw_length": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }
    if raw is None:
        return result

    decoded = raw
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped[:1] in ("{", "["):
            try:
                decoded = json.loads(stripped)
            except (TypeError, ValueError):
                decoded = raw

    result["decoded_type"] = _value_type(decoded)
    if isinstance(decoded, Mapping):
        result["decoded_keys"] = sorted(str(key) for key in decoded)
    safe_fields = _safe_rtk_fields(decoded)
    result["safe_fields"] = sanitize(safe_fields)
    result["quality_fields_found"] = bool(safe_fields)
    return result



def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_value(mapping: Any, *keys: str) -> Any:
    source = _as_mapping(mapping)
    for key in keys:
        if key in source and source.get(key) is not None:
            return source.get(key)
    return None


def _opaque_metadata(value: Any) -> dict[str, Any]:
    """Describe an opaque vendor field without publishing its raw value."""
    if value is None:
        return {"present": False}
    encoded = _rtk_value_bytes(value)
    return {
        "present": True,
        "type": _value_type(value),
        "length": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _firmware_summary(device_info: Any) -> dict[str, Any]:
    info = _as_mapping(device_info)
    nonstandard = _as_mapping(info.get("nonstandardVehicleConfig"))
    versions = _as_mapping(nonstandard.get("firmwareVersion"))
    return {
        "model": info.get("model"),
        "versions": sanitize(dict(versions)),
        "has_screen": nonstandard.get("hasScreen"),
    }


def _battery_summary(index2: Any, device_info: Any, set_list: Any, maintenance: Any) -> dict[str, Any]:
    status = _as_mapping(index2)
    settings = _as_mapping(set_list)
    info = _as_mapping(device_info)
    nonstandard = _as_mapping(info.get("nonstandardVehicleConfig"))
    battery_config = _as_mapping(nonstandard.get("batteryConfig"))
    maintenance_data = _as_mapping(maintenance)
    maintenance_battery = _as_mapping(maintenance_data.get("battery"))
    return {
        "soc_pct": _first_value(status, "soc"),
        "soh_pct": _first_value(status, "soh"),
        "battery_status_raw": _first_value(status, "batteryStatus"),
        "charge_remaining_time_raw": _first_value(status, "chgRemainTimeUser"),
        "reported_max_capacity": _first_value(maintenance_battery, "maxCapacity"),
        "charging_limit_pct": _first_value(settings, "chargingLimit"),
        "return_battery_level_pct": _first_value(settings, "returnBatteryLevel"),
        "recommended_charging_limit_pct": _first_value(battery_config, "chargingLimitRecommend"),
        "recommended_return_battery_level_pct": _first_value(battery_config, "returnBatteryLevelRecommend"),
        "charging_limit_min_pct": _first_value(battery_config, "chargingLimitMin"),
        "charging_limit_max_pct": _first_value(battery_config, "chargingLimitMax"),
        "return_battery_level_min_pct": _first_value(battery_config, "returnBatteryLevelMin"),
        "return_battery_level_max_pct": _first_value(battery_config, "returnBatteryLevelMax"),
    }


def _connectivity_summary(index2: Any, auth_list: Any, coordinator_data: Any, vehicle_type: Any) -> dict[str, Any]:
    status = _as_mapping(index2)
    data = _as_mapping(coordinator_data)
    own_auth: Mapping[str, Any] = {}
    auth_rows = auth_list if isinstance(auth_list, list) else []
    for row in auth_rows:
        if not isinstance(row, Mapping):
            continue
        if vehicle_type is not None and str(row.get("vehicle_type")) == str(vehicle_type):
            own_auth = row
            break
    return {
        "network_type": _first_value(status, "networkType"),
        "network_status": _first_value(status, "network_status"),
        "signal_raw": _first_value(status, "network_signal"),
        "signal_4g_raw": _first_value(status, "network_signal_4G"),
        "signal_wifi_raw": _first_value(status, "network_signal_wifi"),
        "auth_signal_raw": _first_value(own_auth, "network_signal"),
        "auth_network_type": _first_value(own_auth, "networkType"),
        "mqtt_connected": data.get("mqtt_connected"),
        "mqtt_stream_state": data.get("mqtt_stream_state"),
        "mqtt_pose_age_s": data.get("mqtt_pose_age"),
        "mqtt_state_age_s": data.get("mqtt_state_age"),
        "mqtt_action_age_s": data.get("mqtt_action_age"),
        "mqtt_recovery_count": data.get("mqtt_recovery_count"),
        "private_poll_age_s": data.get("private_poll_age"),
        "private_poll_profile": data.get("private_poll_profile"),
    }


def _positioning_summary(location: Any, device_info: Any, set_list: Any, coordinator_data: Any) -> dict[str, Any]:
    info = _as_mapping(device_info)
    settings = _as_mapping(set_list)
    switch_extend = _as_mapping(info.get("switchExtend"))
    data = _as_mapping(coordinator_data)
    return {
        "position_source": data.get("pose_source"),
        "pose_age_s": data.get("mqtt_pose_age"),
        "pose_stream_state": data.get("mqtt_stream_state"),
        "sensor_type": info.get("sensor_type"),
        "antenna_support_num": info.get("antennaSupportNum"),
        "rtk_switch": _first_value(settings, "rtkSwitch"),
        "rtk_switch_capability": _first_value(switch_extend, "rtkSwitch"),
        "rtk_data_source": _first_value(settings, "rtkDataSource"),
        "rtk_visible": _first_value(settings, "rtkVisible"),
        "rtk_visible_country": _first_value(settings, "rtkVisibleCountry"),
        "slam_switch": _first_value(settings, "slamSwitch"),
        "rtk_payload": rtk_diagnostics(location),
    }


def _capability_summary(device_info: Any, set_list: Any) -> dict[str, Any]:
    info = _as_mapping(device_info)
    settings = _as_mapping(set_list)
    mowing = _as_mapping(info.get("mowingExtend"))
    return {
        "map_area_limit_m2": _first_value(info, "map_area_limit"),
        "map_max_area_limit_m2": _first_value(info, "map_max_area_limit"),
        "sub_map_limit": _first_value(info, "sub_map_limit"),
        "vision_off_area_limit": _first_value(info, "visionoff_limit"),
        "mowing_path_width_raw": _first_value(mowing, "mowingPathWidth"),
        "supported_cutting_heights_mm": sanitize(info.get("mowingHeightList")),
        "default_line_speed": _first_value(info, "default_line_speed"),
        "default_rotation_speed": _first_value(info, "default_rotation_speed"),
        "low_cutting_kit_switch": _first_value(_as_mapping(info.get("switchExtend")), "lowCuttingKitSwitch"),
        "terrain_adapt_switch": _first_value(settings, "terrainAdaptSwitch"),
        "traction_control": _first_value(settings, "tractionControl", "tcsSwitch"),
        "narrow_zone_adapt_switch": _first_value(settings, "narrowZoneAdaptSwitch"),
        "edge_sense": _first_value(settings, "edgeSense"),
        "edge_sense_level": _first_value(settings, "edgeSenselevel"),
    }


def _maintenance_summary(maintenance: Any) -> dict[str, Any]:
    source = _as_mapping(maintenance)
    result: dict[str, Any] = {"update_time": source.get("updateTime")}
    for key in ("knife", "chassis"):
        item = _as_mapping(source.get(key))
        result[key] = {
            "default_duration_raw": item.get(f"{key}DefaultDuration"),
            "set_time_raw": item.get("setTime"),
            "used_time_raw": item.get("usedTime"),
            "duration_option_count": len(item.get(f"{key}DurationList") or []),
        }
    return result


def _schedule_summary(set_list: Any, today_plan: Any) -> dict[str, Any]:
    settings = _as_mapping(set_list)
    today = _as_mapping(today_plan)
    plan = settings.get("plan") if isinstance(settings.get("plan"), list) else []
    plan_v2 = settings.get("plan_v2") if isinstance(settings.get("plan_v2"), list) else []
    open_days = sum(1 for row in plan if isinstance(row, Mapping) and row.get("open") in (1, "1", True))
    v2_periods = 0
    v2_periods_with_zones = 0
    for row in plan_v2:
        if not isinstance(row, Mapping):
            continue
        periods = row.get("period") if isinstance(row.get("period"), list) else []
        v2_periods += len(periods)
        for period in periods:
            if isinstance(period, Mapping) and period.get("partition_ids"):
                v2_periods_with_zones += 1
    return {
        "global_enabled_raw": _first_value(settings, "startPlan"),
        "timezone_raw": _first_value(settings, "timezone"),
        "timezone_code_raw": _first_value(settings, "timezoneCode"),
        "dst_switch": _first_value(settings, "dstSwitch"),
        "is_dst": _first_value(settings, "isDst"),
        "plan_day_count": len(plan),
        "open_day_count": open_days,
        "plan_v2_day_count": len(plan_v2),
        "plan_v2_period_count": v2_periods,
        "plan_v2_periods_with_zones": v2_periods_with_zones,
        "today_weekday_raw": _first_value(today, "weekDay"),
        "today_plan_status_raw": _first_value(today, "c_plan_status"),
        "today_task_status_raw": _first_value(today, "m_task_status"),
        "today_start_raw": _first_value(today, "c_plan_s_time"),
        "today_end_raw": _first_value(today, "c_plan_e_time"),
        "today_partition_length_raw": _first_value(today, "partition_length"),
    }


def _environment_summary(set_list: Any) -> dict[str, Any]:
    settings = _as_mapping(set_list)
    keys = (
        "animalProtection",
        "nightAnimalProtection",
        "rainDetectionSwitch",
        "rainSensor",
        "rainSensitivity",
        "weatherSwitch",
        "weatherSensitivity",
        "frostSwitch",
        "frostDelayTime",
        "snowSwitch",
        "snowDelayTime",
        "stormSwitch",
        "highTempSwitch",
        "allowMaxTemp",
        "childLock",
        "liftSwitch",
        "guard",
        "dndModeSwitch",
        "powerSaveShutdownSwitch",
    )
    return {key: settings.get(key) for key in keys if key in settings}


def _opaque_vendor_summary(index2: Any, location: Any) -> dict[str, Any]:
    status = _as_mapping(index2)
    position = _as_mapping(location)
    return {
        "bool_state": _opaque_metadata(status.get("boolState")),
        "feature_bitmap": _opaque_metadata(status.get("fun_support")),
        "index_map_work_position": _opaque_metadata(status.get("map_work_position")),
        "location_map_work_position": _opaque_metadata(position.get("map_work_position")),
        "partition_id_list": _opaque_metadata(status.get("partitionIdList")),
        "permanent_data_count": len(status.get("permanent_data") or []),
    }


def sanitize(value: Any, *, key: str | None = None) -> Any:
    """Recursively sanitize account/location identifiers while retaining structure."""
    if key is not None and _is_sensitive_key(key):
        return "<redacted>"

    if isinstance(value, Mapping):
        return {str(k): sanitize(v, key=str(k)) for k, v in value.items()}

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize(item) for item in value]

    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        return {
            "_omitted": "bytes",
            "length": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("http://", "https://", "ws://", "wss://")):
            return _safe_url(stripped)
        # map_detail and some settings are JSON encoded inside a string. Decode
        # those so the export contains every key rather than one opaque blob.
        if stripped[:1] in ("{", "["):
            try:
                decoded = json.loads(stripped)
            except (TypeError, ValueError):
                decoded = None
            if decoded is not None:
                return sanitize(decoded)
        # Avoid writing huge compressed/base64 resources into diagnostics. Their
        # existence, length and hash are enough to identify changes between runs.
        if len(value) > 16_384:
            return _large_value_summary(value)
        return value

    if value is None or isinstance(value, (bool, int, float)):
        return value

    return repr(value)


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def inventory(value: Any) -> dict[str, Any]:
    """Return all nested key paths, types and keyword-focused paths."""
    paths: dict[str, set[str]] = {}

    def walk(current: Any, path: str) -> None:
        paths.setdefault(path or "$", set()).add(_value_type(current))
        if isinstance(current, dict):
            for child_key, child_value in current.items():
                child = f"{path}.{child_key}" if path else str(child_key)
                walk(child_value, child)
        elif isinstance(current, list):
            for child_value in current[:25]:
                child = f"{path}[]" if path else "[]"
                walk(child_value, child)

    walk(value, "")
    key_paths = [
        {"path": path, "types": sorted(types)}
        for path, types in sorted(paths.items())
    ]
    keyword_paths = {
        keyword: [item["path"] for item in key_paths if keyword in item["path"].lower()]
        for keyword in _KEYWORDS
    }
    return {
        "key_count": len(key_paths),
        "key_paths": key_paths,
        "keyword_paths": {key: values for key, values in keyword_paths.items() if values},
    }


async def _read(
    hass: HomeAssistant, func, *args
) -> tuple[dict[str, Any], Any]:
    try:
        data = await hass.async_add_executor_job(func, *args)
    except Exception as err:  # noqa: BLE001 - diagnostics records each failure
        return (
            {
                "ok": False,
                "error_type": type(err).__name__,
                "error": str(err),
            },
            None,
        )
    clean = sanitize(data)
    return (
        {
            "ok": True,
            "data": clean,
            "inventory": inventory(clean),
        },
        data,
    )


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp, path)


async def async_build_diagnostics(
    hass: HomeAssistant,
    coordinator: Any,
    *,
    include_compressed_map: bool = True,
) -> dict[str, Any]:
    """Collect and return sanitized read-only diagnostics in memory."""
    client = coordinator.client
    sn = coordinator.sn
    vehicle_type = coordinator.vehicle_type

    endpoint_specs = (
        ("auth_list", client.auth_list, ()),
        ("index2", client.index2, (sn,)),
        ("device_info", client.device_info, (sn,)),
        ("set_list", client.set_list, (sn,)),
        ("vehicle_config", client.vehicle_config, (sn,)),
        ("today_plan", client.today_plan, (sn, vehicle_type)),
        ("location", client.location, (sn, vehicle_type)),
        ("errors", client.errors, (sn, vehicle_type)),
        ("maintenance", client.maintenance, (sn,)),
        ("map_list", client.map_list, (sn,)),
        ("path_info_time", client.path_info_time, (sn,)),
    )

    endpoints: dict[str, Any] = {}
    raw_endpoint_data: dict[str, Any] = {}
    for name, func, args in endpoint_specs:
        result, raw = await _read(hass, func, *args)
        endpoints[name] = result
        raw_endpoint_data[name] = raw

    map_id, map_base_id, map_edit_time = resolve_map_identifiers(
        raw_endpoint_data.get("location"), raw_endpoint_data.get("map_list")
    )
    if map_id and map_base_id:
        endpoints["map_detail_plain"], _ = await _read(
            hass, client.map_detail_plain, sn, map_id, map_base_id
        )
        endpoints["station_map"], _ = await _read(
            hass, client.station_map, sn, map_id, map_base_id
        )
        if include_compressed_map:
            endpoints["map_detail_compressed"], _ = await _read(
                hass, client.map_detail, sn, map_id, map_base_id
            )
    else:
        endpoints["map_detail_plain"] = {
            "ok": False,
            "error_type": "MissingMapIdentifiers",
            "error": "No map_id/map_base_id found in location or map_list",
        }

    mqtt_bridge = getattr(coordinator, "mqtt_bridge", None)
    mqtt_inventory = None
    mqtt_health = None
    if mqtt_bridge is not None and hasattr(mqtt_bridge, "diagnostic_inventory"):
        mqtt_inventory = mqtt_bridge.diagnostic_inventory()
    if mqtt_bridge is not None and hasattr(mqtt_bridge, "diagnostic_health"):
        mqtt_health = mqtt_bridge.diagnostic_health()
    private_polling = (
        coordinator.polling_diagnostics()
        if hasattr(coordinator, "polling_diagnostics")
        else None
    )

    last_mow_command = (
        coordinator.mow_command_diagnostics()
        if hasattr(coordinator, "mow_command_diagnostics")
        else None
    )
    if isinstance(last_mow_command, dict):
        cmd_num = last_mow_command.get("cmd_num")
        if cmd_num:
            command_status, _ = await _read(
                hass, client.command_status, sn, str(cmd_num)
            )
            last_mow_command["command_status_at_export"] = command_status
        else:
            last_mow_command["command_status_at_export"] = {
                "ok": False,
                "error_type": "MissingCommandNumber",
                "error": "The send response did not expose a command number.",
            }

    now = datetime.now(timezone.utc)
    data = coordinator.data or {}
    map_data = data.get("map") or {}
    diagnostics_detail = str(
        coordinator.entry.options.get(
            OPT_DIAGNOSTICS_DETAIL, DEFAULT_DIAGNOSTICS_DETAIL
        )
    )
    active_history = coordinator.history.active_session
    if isinstance(active_history, dict):
        active_summary = {
            key: active_history.get(key)
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
        active_summary["point_count"] = len(active_history.get("points") or [])
        if diagnostics_detail == "extended":
            active_summary["recent_points"] = sanitize(
                deepcopy((active_history.get("points") or [])[-100:])
            )
    else:
        active_summary = None

    session_index = coordinator.history.sessions_index_payload()
    session_summaries = session_index.get("sessions") or []
    document = {
        "format": "navimower-diagnostics-v2",
        "schema_version": 2,
        "map_api_schema_version": MAP_API_SCHEMA_VERSION,
        "created_utc": now.isoformat(),
        "read_only": True,
        "diagnostics_detail": diagnostics_detail,
        "entry": {
            "data": sanitize(deepcopy(dict(coordinator.entry.data))),
            "options": sanitize(deepcopy(dict(coordinator.entry.options))),
        },
        "rtk": rtk_diagnostics(raw_endpoint_data.get("location")),
        "diagnostic_summaries": {
            "positioning": sanitize(_positioning_summary(
                raw_endpoint_data.get("location"),
                raw_endpoint_data.get("device_info"),
                raw_endpoint_data.get("set_list"),
                data,
            )),
            "connectivity": sanitize(_connectivity_summary(
                raw_endpoint_data.get("index2"),
                raw_endpoint_data.get("auth_list"),
                data,
                vehicle_type,
            )),
            "battery": sanitize(_battery_summary(
                raw_endpoint_data.get("index2"),
                raw_endpoint_data.get("device_info"),
                raw_endpoint_data.get("set_list"),
                raw_endpoint_data.get("maintenance"),
            )),
            "firmware": sanitize(_firmware_summary(raw_endpoint_data.get("device_info"))),
            "capabilities": sanitize(_capability_summary(
                raw_endpoint_data.get("device_info"),
                raw_endpoint_data.get("set_list"),
            )),
            "maintenance": sanitize(_maintenance_summary(raw_endpoint_data.get("maintenance"))),
            "schedule": sanitize(_schedule_summary(
                raw_endpoint_data.get("set_list"),
                raw_endpoint_data.get("today_plan"),
            )),
            "environment_and_safety": sanitize(_environment_summary(raw_endpoint_data.get("set_list"))),
            "opaque_vendor_fields": sanitize(_opaque_vendor_summary(
                raw_endpoint_data.get("index2"),
                raw_endpoint_data.get("location"),
            )),
        },
        "authentication": {
            "private_cloud": (
                "stored private app-cloud session; normal refresh may "
                "reauthenticate if expired"
            ),
            "smart_home_oauth": (
                "Home Assistant managed OAuth token; credentials are not exported"
            ),
        },
        "commands_sent": False,
        "map_writes_performed": False,
        "last_mow_command": sanitize(deepcopy(last_mow_command)),
        "mower": {
            "serial": f"{sn[:3]}***{sn[-4:]}" if len(sn) >= 8 else "***",
            "vehicle_type": vehicle_type,
            "state_code": data.get("state_code"),
            "activity": data.get("activity"),
            "private_cloud_connected": data.get("private_cloud_connected"),
            "private_cloud_error": data.get("private_cloud_error"),
            "oauth_configured": data.get("oauth_configured"),
            "oauth_connected": data.get("oauth_connected"),
            "oauth_error": data.get("oauth_error"),
            "mqtt_configured": data.get("mqtt_configured"),
            "mqtt_connected": data.get("mqtt_connected"),
            "mqtt_error": data.get("mqtt_error"),
            "mqtt_vehicle_state": data.get("mqtt_vehicle_state"),
            "mqtt_action": data.get("mqtt_action"),
            "mqtt_pose_age": data.get("mqtt_pose_age"),
            "mqtt_state_age": data.get("mqtt_state_age"),
            "mqtt_action_age": data.get("mqtt_action_age"),
            "docked": data.get("docked"),
            "docked_source": data.get("docked_source"),
            "mqtt_stream_state": data.get("mqtt_stream_state"),
            "mqtt_recovery_count": data.get("mqtt_recovery_count"),
            "position_source": data.get("pose_source"),
            "private_poll_age": data.get("private_poll_age"),
            "private_poll_profile": data.get("private_poll_profile"),
            "trail_active": data.get("trail_active"),
            "trail_points": len(data.get("trail") or []),
            "current_physical_zone_id": data.get("current_physical_zone_id"),
            "target_zone_ids": data.get("target_zone_ids"),
            "target_zone_source": data.get("target_zone_source"),
            "target_zone_command_source": data.get("target_zone_command_source"),
            "current_channel_id": data.get("current_channel_id"),
            "current_channel_name": data.get("current_channel"),
            "current_channel_source": data.get("current_channel_source"),
            "current_channel_stale": data.get("current_channel_stale"),
            "current_channel_pose_valid": data.get("current_channel_pose_valid"),
            "current_channel_pose_age": data.get("current_channel_pose_age"),
            "telemetry": sanitize(
                {
                    "battery": data.get("battery"),
                    "battery_source": data.get("battery_source"),
                    "battery_source_age": data.get("battery_source_age"),
                    "battery_mqtt": data.get("battery_mqtt"),
                    "battery_mqtt_age": data.get("battery_mqtt_age"),
                    "battery_private_cloud": data.get("battery_private_cloud"),
                    "mowing_progress": data.get("mowing_progress"),
                    "mowing_progress_source": data.get("mowing_progress_source"),
                    "mowing_progress_source_age": data.get(
                        "mowing_progress_source_age"
                    ),
                    "mowing_progress_mqtt": data.get("mowing_progress_mqtt"),
                    "mowing_progress_private_cloud": data.get(
                        "mowing_progress_private_cloud"
                    ),
                    "task_progress_private_cloud": data.get(
                        "task_progress_private_cloud"
                    ),
                    "task_progress_source": data.get("task_progress_source"),
                    "active_zone_progress": data.get("active_zone_progress"),
                    "active_zone_progress_source": data.get(
                        "active_zone_progress_source"
                    ),
                    "active_zone_progress_zone_id": data.get(
                        "active_zone_progress_zone_id"
                    ),
                    "active_zone_progress_source_age": data.get(
                        "active_zone_progress_source_age"
                    ),
                    "work_progress_raw": data.get("work_progress"),
                    "route_progress_raw": data.get("mow_route_progress"),
                    "coverage_raw": data.get("coverage"),
                    "coverage_source": data.get("coverage_source"),
                    "zone_states_revision": data.get("zone_states_revision"),
                    "zone_states": data.get("zone_states"),
                    "totals": data.get("totals"),
                    "session_area": data.get("session_area"),
                    "session_area_source": data.get("session_area_source"),
                    "session_area_source_age": data.get(
                        "session_area_source_age"
                    ),
                    "session_area_mqtt": data.get("session_area_mqtt"),
                    "session_area_private_cloud": data.get(
                        "session_area_private_cloud"
                    ),
                    "total_area": data.get("total_area"),
                    "total_area_source": data.get("total_area_source"),
                    "cycle_value_reset_pending": data.get(
                        "cycle_value_reset_pending"
                    ),
                    "cycle_value_reset_reason": data.get(
                        "cycle_value_reset_reason"
                    ),
                    "cycle_value_reset_age": data.get(
                        "cycle_value_reset_age"
                    ),
                    "schedule_enabled": (data.get("settings") or {}).get(
                        "schedule_enabled"
                    ),
                }
            ),
            "gate_states": sanitize(deepcopy(data.get("gate_states") or {})),
            "gate_arrival_guards": sanitize(
                deepcopy(data.get("gate_arrival_guards") or {})
            ),
        },
        "map_api": {
            "schema_version": MAP_API_SCHEMA_VERSION,
            "resolved_map_id": map_id,
            "resolved_map_base_id": map_base_id,
            "resolved_map_edit_time": map_edit_time,
            "map_loaded": bool(map_data),
            "map_version": map_data.get("version"),
            "map_modified_count": map_data.get("modified_count"),
            "zone_count": len(map_data.get("zones") or []),
            "off_limit_count": len(map_data.get("off_limit_areas") or []),
            "doodle_count": len(map_data.get("doodles") or []),
            "channel_count": len(map_data.get("channels") or []),
            "global_cutting_height_mm": (data.get("settings") or {}).get("cut_height"),
            "global_cutting_height_raw": (data.get("settings") or {}).get("cut_height_raw"),
            "cutting_height_supported": data.get("cutting_height_supported"),
            "zone_details": sanitize(deepcopy(data.get("zone_details") or [])),
            "zone_states_revision": data.get("zone_states_revision"),
            "zone_states": sanitize(deepcopy(data.get("zone_states") or [])),
            "totals": sanitize(deepcopy(data.get("totals") or {})),
            "daily_trails_revision": coordinator.history.trail_revision,
            "doodles": sanitize(deepcopy(map_data.get("doodles") or [])),
            "map_api_path": f"/api/navimower/map/{coordinator.entry.entry_id}",
            "sessions_api_path": (
                f"/api/navimower/sessions/{coordinator.entry.entry_id}"
            ),
            "session_api_path_template": (
                f"/api/navimower/session/{coordinator.entry.entry_id}/{{session_id}}"
            ),
        },
        "history": {
            "retention_days": coordinator.history.retention_days,
            "include_return_trail": coordinator.history.include_return_trail,
            "active_session": sanitize(deepcopy(active_summary)),
            "retained_session_count": len(session_summaries),
            "sessions": sanitize(deepcopy(session_summaries)),
            "zone_history": sanitize(coordinator.history.zone_history()),
            "cycle": sanitize(coordinator.history.cycle_diagnostics()),
            "point_format": list(SESSION_DETAIL_POINT_FORMAT),
        },
        "endpoints": endpoints,
        "private_polling": sanitize(deepcopy(private_polling)),
        "mqtt_health": sanitize(deepcopy(mqtt_health)),
        "mqtt_inventory": sanitize(deepcopy(mqtt_inventory)),
        "notes": [
            "Account, mower, network and physical GPS identifiers are redacted.",
            "PIN, RTK anchor, ICCID and anti-theft location fields are redacted.",
            "The RTK summary exposes only metadata and quality/status candidates, never coordinates.",
            "Large binary/base64 resources are represented by length and SHA-256 only.",
            "Local map X/Y coordinates and vendor doodle SVG are retained for geometry analysis.",
            "Full retained routes remain in authenticated session APIs and HA storage.",
            "commands_sent=false refers to the diagnostics export itself; last_mow_command records the most recent earlier user command.",
            "command_status_at_export is a read-only status lookup for the stored command number.",
        ],
    }

    return document


async def async_export_diagnostics(
    hass: HomeAssistant,
    coordinator: Any,
    *,
    include_compressed_map: bool = True,
) -> str:
    """Write the same sanitized diagnostics used by Home Assistant's UI."""
    document = await async_build_diagnostics(
        hass,
        coordinator,
        include_compressed_map=include_compressed_map,
    )
    now = datetime.now(timezone.utc)
    folder = Path(hass.config.path("navimower_diagnostics"))
    stamp = now.strftime("%Y%m%d_%H%M%S")
    path = folder / f"navimower_diagnostics_{stamp}.json"
    latest = folder / "navimower_diagnostics_latest.json"
    await hass.async_add_executor_job(_write_json, path, document)
    await hass.async_add_executor_job(_write_json, latest, document)
    return str(path)
