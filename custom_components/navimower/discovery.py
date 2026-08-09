"""Safe helpers for temporary passive vendor-protocol discovery."""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_SENSITIVE_EXACT = {
    "access_token", "refresh_token", "token", "password", "pwd", "pwdinfo",
    "secret", "authorization", "cookie", "email", "phone", "uid", "uuid",
    "auth_uid", "user_id", "userid", "username", "user_name", "vehicle_sn",
    "serial", "serial_number", "sn", "device_id", "oauth_device_id", "client_id",
    "ssid", "bssid", "mac", "ip", "ip_address", "iccid", "pin", "pin_code",
    "pincode", "latitude", "longitude", "anchor", "anti_theft_point",
    "antitheftpoint", "origin_gps", "center_gps", "ne_gps", "sw_gps",
}
_OBSERVED_VALUE_KEYS = {
    "type", "action", "subaction", "vehiclestate", "eventcode", "code", "status",
    "state", "messagetype", "notificationtype", "errorcode", "error_code",
    "faultcode", "fault_code", "warningcode", "warning_code", "alarmcode",
    "alarm_code", "event", "eventtype", "event_type", "notification",
    "message", "title", "reason",
}
_SIGNAL_KEY_MARKERS = (
    "error", "fault", "event", "notification", "message", "title", "warning",
    "alarm", "reason", "code",
)
_CODE_TOKEN_RE = re.compile(r"(?i)(?<![0-9a-f])([0-9a-f]{3,8})(?![0-9a-f])")
_MAX_DEPTH = 8
_MAX_ITEMS = 50
_MAX_STRING = 512
_MAX_OBSERVED_VALUE = 180


def mqtt_discovery_topic(device_id: str) -> str:
    """Return the legacy current mower-only discovery wildcard."""
    return f"/downlink/vehicle/{device_id}/#"


def mqtt_discovery_topics(device_id: str) -> tuple[str, ...]:
    """Return wider opt-in downlink subscriptions for notification research."""
    del device_id
    return ("/downlink/#",)


def _normalize_key(key: str) -> str:
    return str(key).strip().lower().replace("-", "_")


def _compact_key(key: str) -> str:
    return "".join(ch for ch in _normalize_key(key) if ch.isalnum())


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    return (
        normalized in _SENSITIVE_EXACT
        or "token" in normalized
        or "password" in normalized
        or "secret" in normalized
        or normalized.endswith("_uid")
        or normalized.endswith("_uuid")
        or "latitude" in normalized
        or "longitude" in normalized
        or normalized.endswith("_gps")
        or normalized.startswith("gps_")
    )


def _safe_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<redacted-url>"
    if parsed.scheme not in {"http", "https", "ws", "wss", "rtsp", "rtsps"} or not parsed.netloc:
        return value
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme, f"{host}{port}", parsed.path, "", ""))


def _large_summary(raw: bytes, kind: str) -> dict[str, Any]:
    return {
        "_omitted": kind,
        "length": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def sanitize_discovery_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Return a bounded JSON-safe sample with credentials/location IDs redacted."""
    if key and _is_sensitive_key(key):
        return "<redacted>"
    if depth >= _MAX_DEPTH:
        return "<max-depth>"
    if isinstance(value, Mapping):
        out = {}
        for index, (raw_key, child) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                out["_truncated_keys"] = len(value) - _MAX_ITEMS
                break
            child_key = str(raw_key)
            out[child_key] = sanitize_discovery_value(
                child, key=child_key, depth=depth + 1
            )
        return out
    if isinstance(value, (list, tuple)):
        out = [
            sanitize_discovery_value(child, depth=depth + 1)
            for child in value[:_MAX_ITEMS]
        ]
        if len(value) > _MAX_ITEMS:
            out.append({"_truncated_items": len(value) - _MAX_ITEMS})
        return out
    if isinstance(value, (bytes, bytearray)):
        return _large_summary(bytes(value), "binary")
    if isinstance(value, str):
        safe = _safe_url(value)
        if safe != value:
            return safe
        raw = value.encode("utf-8", errors="replace")
        if len(raw) > _MAX_STRING:
            return _large_summary(raw, "large_string")
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    rendered = repr(value).encode("utf-8", errors="replace")
    if len(rendered) > _MAX_STRING:
        return _large_summary(rendered, "repr")
    return rendered.decode("utf-8", errors="replace")


def sanitize_discovery_payload(payload: bytes) -> Any:
    """Decode one MQTT payload and retain only a safe bounded sample."""
    raw = bytes(payload or b"")
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except (TypeError, ValueError):
        return _large_summary(raw, "non_json")
    return sanitize_discovery_value(parsed)


def _signal_key(key: str) -> bool:
    compact = _compact_key(key)
    return compact in {_compact_key(item) for item in _OBSERVED_VALUE_KEYS} or any(
        marker in compact for marker in _SIGNAL_KEY_MARKERS
    )


def _render_observed(key: str, value: Any) -> str | None:
    if _is_sensitive_key(key) or isinstance(value, (Mapping, list, tuple, bytes, bytearray)):
        return None
    safe = sanitize_discovery_value(value, key=key)
    if isinstance(safe, (dict, list)):
        return None
    rendered = " ".join(str(safe).split())
    if len(rendered) > _MAX_OBSERVED_VALUE:
        rendered = rendered[: _MAX_OBSERVED_VALUE - 3] + "..."
    return rendered


def _code_candidates(value: Any) -> set[str]:
    if value is None or isinstance(value, (Mapping, list, tuple, bytes, bytearray)):
        return set()
    text = str(value).upper()
    out: set[str] = set()
    for match in _CODE_TOKEN_RE.finditer(text):
        token = match.group(1).upper()
        if any(ch.isdigit() for ch in token):
            out.add(token)
    return out


def structure_summary(value: Any) -> dict[str, list[str]]:
    """Describe JSON structure and retain bounded notification/error signal values."""
    parsed_types: set[str] = set()
    top_level_keys: set[str] = set()
    key_paths: set[str] = set()
    observed_values: set[str] = set()
    code_candidates: set[str] = set()

    def walk(current: Any, path: str = "", depth: int = 0) -> None:
        if depth >= _MAX_DEPTH:
            return
        parsed_types.add(type(current).__name__)
        if isinstance(current, Mapping):
            for index, (raw_key, child) in enumerate(current.items()):
                if index >= _MAX_ITEMS:
                    break
                key = str(raw_key)
                child_path = f"{path}.{key}" if path else key
                key_paths.add(child_path)
                if not path:
                    top_level_keys.add(key)
                compact = _compact_key(key)
                legacy_observed = compact in {_compact_key(item) for item in _OBSERVED_VALUE_KEYS}
                if (legacy_observed or _signal_key(key)) and isinstance(child, (str, int, float, bool)):
                    rendered = _render_observed(key, child)
                    if rendered is not None:
                        observed_values.add(f"{key}={rendered}")
                    code_candidates.update(_code_candidates(child))
                walk(child, child_path, depth + 1)
        elif isinstance(current, (list, tuple)):
            for child in current[:_MAX_ITEMS]:
                walk(child, f"{path}[]" if path else "[]", depth + 1)

    walk(value)
    observed_values.update(f"code_candidate={code}" for code in code_candidates)
    return {
        "parsed_types": sorted(parsed_types),
        "top_level_keys": sorted(top_level_keys),
        "key_paths": sorted(key_paths),
        "observed_type_values": sorted(observed_values),
    }
