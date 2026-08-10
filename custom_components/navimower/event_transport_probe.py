"""Read-only notification transport discovery for explicit action diagnostics.

Beta22 tests whether the missing Navimow notification timeline lives behind a
separate host, HTTP method, or request encoding. It is intentionally separate
from normal polling and Home Assistant's native Download diagnostics flow.

Authenticated variants keep the existing p:101 business payload encrypted.
Plain variants are deliberately unauthenticated and contain only harmless
language/pagination fields; credentials are never placed in query parameters or
plaintext request bodies.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .api import crypto
from .diagnostics_export import inventory, sanitize

_PATHS: tuple[str, ...] = (
    "/message/message/list",
    "/message/notification/list",
    "/message/center/list",
    "/push/message/list",
    "/push/notification/list",
)


def _hosts(client: Any) -> tuple[tuple[str, str], ...]:
    region = str(getattr(client, "region", "fra") or "fra").lower()
    return (
        ("p101", f"https://navimow-{region}.ninebot.com"),
        ("h5", f"https://navimow-h5-{region}.willand.com"),
    )


def _device_params(sn: str, vehicle_type: Any, language: str) -> dict[str, Any]:
    return {
        "vehicle_sn": sn,
        "vehicle_type": vehicle_type,
        "language": language,
        "page": 1,
        "pageSize": 20,
    }


def _plain_params(language: str) -> dict[str, Any]:
    return {"language": language, "page": 1, "pageSize": 20}


def _body_summary(raw: bytes, content_type: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "body_length": len(raw),
        "body_sha256": hashlib.sha256(raw).hexdigest(),
        "content_type": content_type,
    }
    if not raw:
        return result
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        result["body_kind"] = "non_json"
        return result

    result["body_kind"] = "json"
    decoded = crypto.decode_response(parsed) if isinstance(parsed, dict) else parsed
    clean = sanitize(decoded)
    result["response_type"] = type(decoded).__name__
    result["inventory"] = inventory(clean)
    if isinstance(decoded, dict):
        if "code" in decoded:
            result["business_code"] = sanitize(decoded.get("code"))
        if "desc" in decoded:
            result["description"] = sanitize(decoded.get("desc"))
        if decoded.get("code") == 1:
            data = sanitize(decoded.get("data"))
            result["business_ok"] = True
            result["data_inventory"] = inventory(data)
            if isinstance(data, list):
                result["sample"] = data[:2]
            elif isinstance(data, dict):
                result["sample"] = {key: data[key] for key in list(data)[:12]}
    return result


def _request(
    host: str,
    path: str,
    *,
    method: str,
    encoding: str,
    envelope: dict[str, Any] | None = None,
    plain: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"ninebot-version": "1"}
    url = host + path
    data: bytes | None = None

    if encoding == "p101_json_text_html":
        headers["Content-Type"] = "text/html"
        data = json.dumps(envelope or {}, separators=(",", ":")).encode()
    elif encoding == "p101_json_application_json":
        headers["Content-Type"] = "application/json"
        data = json.dumps(envelope or {}, separators=(",", ":")).encode()
    elif encoding == "p101_form":
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode(envelope or {}).encode()
    elif encoding == "p101_query":
        url += "?" + urllib.parse.urlencode(envelope or {})
    elif encoding == "plain_json":
        headers["Content-Type"] = "application/json"
        data = json.dumps(plain or {}, separators=(",", ":")).encode()
    elif encoding == "plain_query":
        url += "?" + urllib.parse.urlencode(plain or {})
    else:  # pragma: no cover - defensive contract
        raise ValueError(f"unsupported probe encoding: {encoding}")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
            out = {
                "http_status": getattr(resp, "status", 200),
                "http_ok": True,
            }
            out.update(_body_summary(raw, str(resp.headers.get("Content-Type", ""))))
            return out
    except urllib.error.HTTPError as err:
        raw = err.read()
        out = {"http_status": err.code, "http_ok": False}
        out.update(_body_summary(raw, str(err.headers.get("Content-Type", ""))))
        return out
    except urllib.error.URLError as err:
        return {
            "http_ok": False,
            "transport_error": sanitize(str(err.reason)),
        }
    except Exception as err:  # noqa: BLE001 - bounded diagnostics discovery
        return {
            "http_ok": False,
            "transport_error": sanitize(f"{type(err).__name__}: {err}"),
        }


def probe_event_transports(client: Any, sn: str, vehicle_type: Any) -> dict[str, Any]:
    """Try bounded read-only host/method/encoding variants for notifications."""
    language = str(getattr(client, "_language", "en") or "en")  # noqa: SLF001
    business = client._auth_body(_device_params(sn, vehicle_type, language))  # noqa: SLF001
    envelope = crypto.pack(deepcopy(business))
    plain = _plain_params(language)

    variants = (
        ("POST", "p101_json_text_html", True),
        ("POST", "p101_json_application_json", True),
        ("POST", "p101_form", True),
        ("GET", "p101_query", True),
        ("POST", "plain_json", False),
        ("GET", "plain_query", False),
    )

    attempts: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    not_found_count = 0

    for host_name, host in _hosts(client):
        for path in _PATHS:
            path_interesting = False
            for method, encoding, authenticated in variants:
                result = _request(
                    host,
                    path,
                    method=method,
                    encoding=encoding,
                    envelope=envelope if authenticated else None,
                    plain=plain if not authenticated else None,
                )
                status = result.get("http_status")
                if status is not None:
                    status_counts[str(status)] = status_counts.get(str(status), 0) + 1
                if status == 404:
                    not_found_count += 1
                    continue

                row = {
                    "host": host_name,
                    "host_url": host,
                    "path": path,
                    "method": method,
                    "encoding": encoding,
                    "authenticated_encrypted": authenticated,
                    **result,
                }
                attempts.append(row)

                interesting = (
                    bool(result.get("business_ok"))
                    or status not in (None, 404, 502)
                    or result.get("body_kind") == "json"
                    and result.get("business_code") not in (None, 502)
                )
                if interesting:
                    path_interesting = True
                    matches.append(
                        {
                            "host": host_name,
                            "path": path,
                            "method": method,
                            "encoding": encoding,
                            "http_status": status,
                            "business_code": result.get("business_code"),
                            "business_ok": result.get("business_ok", False),
                            "inventory": result.get("data_inventory")
                            or result.get("inventory"),
                        }
                    )

                # A real successful business payload is enough for this path/host.
                if result.get("business_ok"):
                    break

            # Keep probing other hosts even if one transport is interesting; host
            # comparison is the purpose of beta22.
            _ = path_interesting

    return {
        "read_only": True,
        "normal_polling_unchanged": True,
        "native_download_diagnostics_unchanged": True,
        "candidate_path_count": len(_PATHS),
        "host_count": len(_hosts(client)),
        "hosts": [name for name, _host in _hosts(client)],
        "transport_variants": [
            f"{method}:{encoding}" for method, encoding, _auth in variants
        ],
        "credential_safety": (
            "Authenticated variants keep uid/access_token/device_id inside the "
            "encrypted p:101 business payload. Plain variants are unauthenticated "
            "and contain only language/page/pageSize."
        ),
        "attempt_count": len(attempts) + not_found_count,
        "retained_attempt_count": len(attempts),
        "not_found_count": not_found_count,
        "http_status_counts": status_counts,
        "matches": matches,
        "attempts": attempts,
        "note": (
            "Beta22 pivots from parameter guessing to host, HTTP method and "
            "request-encoding discovery. 404 rows are summarized only."
        ),
    }
