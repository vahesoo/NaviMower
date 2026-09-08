"""Sanitization helpers for support diagnostics, never the explicit raw export.

Use word boundaries as well as known aliases: ``editMapUid`` must be hidden,
while ``mapping``, ``map_id`` and capability ranges must remain useful. This
follows the diagnostics-hardening direction of navimow_pro v0.5.1, with extra
handling for acronym keys, JSON strings, URLs and free-text credentials.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import ipaddress
import json
import math
import re
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

REDACTED = "<redacted>"
REDACTION_VERSION = 3
_MAX_DEPTH = 32
_MAX_STRING = 16_384

_EXACT_SENSITIVE_KEYS = {
    "access_token", "refresh_token", "token", "password", "passwd", "pwd",
    "pwdinfo", "secret", "authorization", "cookie", "email", "phone", "uid",
    "uuid", "auth_uid", "user_id", "userid", "username", "user_name",
    "vehicle_sn", "serial", "serial_number", "sn", "device_id",
    "oauth_device_id", "client_id", "ssid", "bssid", "mac", "ip",
    "ip_address", "iccid", "imei", "imsi", "msisdn", "pin", "pin_code",
    "pincode", "rtk", "anchor", "anti_theft_point", "antitheftpoint",
    "latitude", "longitude", "last_latitude", "last_longitude", "origin_gps",
    "center_gps", "ne_gps", "sw_gps", "api_key", "session_key",
    "client_key", "private_key", "encryption_key", "signing_key",
}
_SENSITIVE_WORDS = frozenset({
    "latitude", "longitude", "lat", "lng", "lon", "gps", "coord", "coords",
    "token", "serial", "sn", "uid", "uuid", "imei", "iccid", "iccids",
    "imsi", "msisdn", "email", "mail", "username", "password", "passwd",
    "pwd", "pin", "pincode", "secret", "theft", "authorization", "cookie",
    "ssid", "bssid", "mac", "ip", "phone",
})
_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_WORD_BOUNDARY = re.compile(r"[^A-Za-z0-9]+")
_COMPACT_SENSITIVE_KEYS = frozenset(
    re.sub(r"[^a-z0-9]", "", key.lower()) for key in _EXACT_SENSITIVE_KEYS
)
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"']+")
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[a-z0-9.-]+\.[a-z]{2,}(?![\w.-])")
_BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/-]+=*")
_UUID = re.compile(r"(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b")
_MAC = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f])")
_IPV4 = re.compile(r"(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])")
_JWT = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?![A-Za-z0-9_-])")
_OPAQUE = re.compile(r"[A-Za-z0-9_+/=-]{256,}\Z")
_LITERAL_SKIP = frozenset({REDACTED, "**REDACTED**", "<redacted-url>", "unknown", "unavailable", "Bearer", "bearer", "private_cloud"})
_ASSIGNMENT = re.compile(
    r"(?P<prefix>(?P<key>[A-Za-z][A-Za-z0-9_.-]*)[\"']?\s*[:=]\s*)"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;}\]]+)"
)


def _key_words(key: str) -> tuple[str, ...]:
    text = _ACRONYM_BOUNDARY.sub(r"\1_\2", str(key))
    text = _CAMEL_BOUNDARY.sub(r"\1_\2", text)
    return tuple(word.lower() for word in _WORD_BOUNDARY.split(text) if word)


def _is_sensitive_key(key: str) -> bool:
    words = _key_words(key)
    compact = "".join(words)
    if compact in _COMPACT_SENSITIVE_KEYS or _SENSITIVE_WORDS.intersection(words):
        return True
    # Preserve the previous protection for unseparated credential names too.
    if any(marker in compact for marker in ("token", "password", "secret", "latitude", "longitude")):
        return True
    word_set = set(words)
    if "key" in word_set and word_set.intersection({"api", "session", "client", "private", "encryption", "signing"}):
        return True
    return bool("id" in word_set and word_set.intersection({"device", "user", "client", "account", "vehicle", "mower"}))


def _safe_url(value: str) -> str:
    """Keep the service origin only; paths may contain signed or identifying data."""
    try:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.hostname:
            return "<redacted-url>"
        host = parsed.hostname
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            host = "redacted-host"
        port = f":{parsed.port}" if parsed.port else ""
        return urlunsplit((parsed.scheme, f"{host}{port}", "", "", ""))
    except (TypeError, ValueError):
        return "<redacted-url>"


def _large_value_summary(value: str) -> dict[str, Any]:
    raw = value.encode("utf-8", errors="replace")
    return {"_omitted": "large_string", "length": len(value), "sha256": hashlib.sha256(raw).hexdigest()}


def _json_value(value: str) -> Any:
    if value.lstrip()[:1] not in ("{", "["):
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError, RecursionError):
        return None


def _sensitive_literals(value: Any, *, depth: int = 0, sensitive: bool = False) -> set[str]:
    """Find repeated identifiers before field redaction removes their context.

    Do not globally replace short PINs or numeric GPS values: doing so could
    corrupt unrelated error codes and measurements. Those are removed by field
    names and labelled-text rules. Long textual credentials and identifiers are
    also removed if echoed elsewhere, including dictionary keys and URLs.
    """
    if depth >= _MAX_DEPTH:
        return set()
    out: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            out.update(_sensitive_literals(child, depth=depth + 1, sensitive=sensitive or _is_sensitive_key(str(key))))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            out.update(_sensitive_literals(child, depth=depth + 1, sensitive=sensitive))
    elif isinstance(value, str):
        if sensitive and 6 <= len(value) <= _MAX_STRING and value not in _LITERAL_SKIP:
            out.add(value)
            out.add(quote(value, safe=""))
        elif len(value) <= _MAX_STRING:
            decoded = _json_value(value)
            if decoded is not None:
                out.update(_sensitive_literals(decoded, depth=depth + 1))
    return out


def _safe_text(value: str, literals: tuple[str, ...]) -> str:
    text = _URL.sub(lambda match: _safe_url(match.group()), value)
    text = _BEARER.sub(lambda match: f"{match.group(1)} {REDACTED}", text)
    text = _JWT.sub(REDACTED, text)
    text = _EMAIL.sub(REDACTED, text)
    text = _UUID.sub(REDACTED, text)
    text = _MAC.sub(REDACTED, text)

    def address(match: re.Match[str]) -> str:
        try:
            ipaddress.ip_address(match.group())
        except ValueError:
            return match.group()
        return REDACTED

    text = _IPV4.sub(address, text)
    text = _ASSIGNMENT.sub(
        lambda match: match.group("prefix") + REDACTED
        if _is_sensitive_key(match.group("key")) else match.group(),
        text,
    )
    for literal in literals:
        text = text.replace(literal, REDACTED)
    return text


def sanitize(
    value: Any,
    *,
    key: str | None = None,
    sensitive_values: Any = (),
    exclude_keys: frozenset[str] = frozenset(),
) -> Any:
    """Return a new redacted JSON-safe object without modifying live/raw state.

    ``sensitive_values`` supplies additional local context (for example entry
    tokens) for values already removed from individual report sections. The
    optional exclusions are used only by Download diagnostics to omit retired
    research blocks; the explicit raw-data export does not call this function.
    """
    literals = tuple(sorted(
        _sensitive_literals(value) | _sensitive_literals(sensitive_values),
        key=lambda item: (-len(item), item),
    ))
    omitted_keys = {"".join(_key_words(item)) for item in exclude_keys}

    def walk(current: Any, field: str | None = None, depth: int = 0) -> Any:
        if field is not None and _is_sensitive_key(field):
            return REDACTED
        if depth >= _MAX_DEPTH:
            return {"_omitted": "max_depth"}
        if isinstance(current, Mapping):
            result: dict[str, Any] = {}
            for raw_key, child in current.items():
                child_key = str(raw_key)
                if "".join(_key_words(child_key)) in omitted_keys:
                    continue
                safe_key = _safe_text(child_key, literals)
                if safe_key in result:
                    # Distinct private identifiers can redact to the same key.
                    suffix = 2
                    while f"{safe_key}#{suffix}" in result:
                        suffix += 1
                    safe_key = f"{safe_key}#{suffix}"
                result[safe_key] = walk(child, child_key, depth + 1)
            return result
        if isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            return [walk(child, depth=depth + 1) for child in current]
        if isinstance(current, (bytes, bytearray)):
            raw = bytes(current)
            return {"_omitted": "bytes", "length": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        if isinstance(current, str):
            if len(current) > _MAX_STRING:
                return _large_value_summary(current)
            decoded = _json_value(current)
            if decoded is not None:
                return walk(decoded, depth=depth + 1)
            if _OPAQUE.fullmatch(current.strip()):
                return {**_large_value_summary(current), "_omitted": "opaque_string"}
            return _safe_text(current, literals)
        if isinstance(current, float) and not math.isfinite(current):
            return None
        if current is None or isinstance(current, (bool, int, float)):
            return current
        # repr() can contain credentials or a complete private object dump.
        return {"_omitted": "unsupported_type", "type": type(current).__name__}

    return walk(value, key)
