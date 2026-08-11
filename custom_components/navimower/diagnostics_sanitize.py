"""Sanitization helpers for Home Assistant Download diagnostics."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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
    """Keep only the non-sensitive location portion of a URL-like value."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<redacted-url>"
    if not parsed.scheme:
        return "<redacted-url>"
    host = parsed.hostname or parsed.netloc or ""
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        port = ""
    location = f"{host}{port}" if host else parsed.netloc
    return urlunsplit((parsed.scheme, location, parsed.path, "", ""))


def _large_value_summary(value: str) -> dict[str, Any]:
    raw = value.encode("utf-8", errors="replace")
    return {
        "_omitted": "large_string",
        "length": len(value),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def sanitize(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact account, mower, network and GPS identifiers."""
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
        if "://" in stripped:
            return _safe_url(stripped)
        if stripped[:1] in ("{", "["):
            try:
                decoded = json.loads(stripped)
            except (TypeError, ValueError):
                decoded = None
            if decoded is not None:
                return sanitize(decoded)
        if len(value) > 16_384:
            return _large_value_summary(value)
        return value

    if value is None or isinstance(value, (bool, int, float)):
        return value

    return repr(value)
