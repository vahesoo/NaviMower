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


_KEYWORDS = (
    "angle",
    "boundary",
    "camera",
    "cbox",
    "clock",
    "direction",
    "edge",
    "height",
    "image",
    "img",
    "laser",
    "lidar",
    "map",
    "mow",
    "path",
    "resource",
    "scene",
    "sha",
    "snapshot",
    "terrain",
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
    "client_id",
    "ssid",
    "bssid",
    "mac",
    "ip",
    "ip_address",
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


def _first_map_ids(location: Any, map_list: Any) -> tuple[str | None, str | None]:
    candidates: list[dict[str, Any]] = []
    if isinstance(location, dict):
        candidates.append(location)
    if isinstance(map_list, list):
        candidates.extend(item for item in map_list if isinstance(item, dict))
    elif isinstance(map_list, dict):
        candidates.append(map_list)
        rows = map_list.get("list")
        if isinstance(rows, list):
            candidates.extend(item for item in rows if isinstance(item, dict))
    for item in candidates:
        map_id = item.get("map_id") or item.get("mapId")
        base_id = item.get("map_base_id") or item.get("mapBaseId")
        if map_id is not None and base_id is not None:
            return str(map_id), str(base_id)
    return None, None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp, path)


async def async_export_diagnostics(
    hass: HomeAssistant,
    coordinator: Any,
    *,
    include_compressed_map: bool = True,
) -> str:
    """Collect all known read endpoints and write a sanitized JSON export."""
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
    raw_for_ids: dict[str, Any] = {}
    for name, func, args in endpoint_specs:
        result, raw = await _read(hass, func, *args)
        endpoints[name] = result
        if name in {"location", "map_list"}:
            raw_for_ids[name] = raw

    map_id, map_base_id = _first_map_ids(
        raw_for_ids.get("location"), raw_for_ids.get("map_list")
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
    if mqtt_bridge is not None and hasattr(mqtt_bridge, "diagnostic_inventory"):
        mqtt_inventory = mqtt_bridge.diagnostic_inventory()

    now = datetime.now(timezone.utc)
    document = {
        "format": "navimower-diagnostics-v1",
        "created_utc": now.isoformat(),
        "read_only": True,
        "authentication": (
            "existing client session; automatic reauthentication may occur if expired"
        ),
        "commands_sent": False,
        "map_writes_performed": False,
        "mower": {
            "serial": f"{sn[:3]}***{sn[-4:]}" if len(sn) >= 8 else "***",
            "vehicle_type": vehicle_type,
            "state_code": (coordinator.data or {}).get("state_code"),
            "private_cloud_connected": (coordinator.data or {}).get(
                "private_cloud_connected"
            ),
            "mqtt_connected": (coordinator.data or {}).get("mqtt_connected"),
        },
        "endpoints": endpoints,
        "mqtt_inventory": sanitize(deepcopy(mqtt_inventory)),
        "notes": [
            "Values that identify an account, mower, network or physical GPS location are redacted.",
            "Large binary/base64 resources are represented by length and SHA-256 only.",
            "Local map X/Y coordinates are retained for geometry analysis.",
        ],
    }

    folder = Path(hass.config.path("navimower_diagnostics"))
    stamp = now.strftime("%Y%m%d_%H%M%S")
    path = folder / f"navimower_diagnostics_{stamp}.json"
    latest = folder / "navimower_diagnostics_latest.json"
    await hass.async_add_executor_job(_write_json, path, document)
    await hass.async_add_executor_job(_write_json, latest, document)
    return str(path)
