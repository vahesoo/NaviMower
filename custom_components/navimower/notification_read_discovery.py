"""Read-only H5 discovery for notification read-state mutations.

0.4.2-beta1 deliberately does not call any notification mutation endpoint.  The
only goal of this module is to inspect public Navimow H5 JavaScript when Home
Assistant's Download diagnostics action is used, so field diagnostics can reveal
how the official app calls ``clearBatchMessageRead`` and whether the same code
supports one-message and all-message read operations.

No Navimow credentials, account identifiers, mower serials or encrypted p:101
payloads are sent to H5.  Source bodies are not retained; diagnostics store only
bounded source context around high-signal notification-read terms.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .diagnostics_sanitize import sanitize

_ENTRY_PATHS: tuple[str, ...] = (
    "/message/message/list",
    "/old/",
)
_TARGET_TERMS: tuple[str, ...] = (
    "clearBatchMessageRead",
    "vehicleMessageListField",
    "queryUnreadRedCountForVehicle",
    "getUnreadMessageAndRedCount",
    "get-vehicle-jump-target",
)
_THEME_TERMS: tuple[str, ...] = (
    "message",
    "notification",
    "messagelist",
    "messagecenter",
    "unread",
    "clearbatchmessageread",
)
_MAX_HTML_BYTES = 256 * 1024
_MAX_JS_BYTES = 2 * 1024 * 1024
_MAX_ROOT_ASSETS = 8
_MAX_DYNAMIC_ASSETS = 8
_MAX_CONTEXTS = 32
_MAX_REQUESTS = 48
_CONTEXT_RADIUS = 4200
_DYNAMIC_RADIUS = 2400
_TIMEOUT_SECONDS = 5

_SCRIPT_RE = re.compile(r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
_JS_RE = re.compile(r"[\"']([^\"'\r\n]{1,360}\.js(?:\?[^\"'\r\n]{0,120})?)[\"']", re.I)
_MOWERBOT_RE = re.compile(r"[\"'](/mowerbot/[^\"'\r\n]{1,260})[\"']", re.I)
_HTTP_RE = re.compile(r"method\s*:\s*[\"']?(GET|POST|PUT|DELETE|PATCH)[\"']?", re.I)
_SKIP_RE = re.compile(r"skipEncryption\s*:\s*(true|false)", re.I)
_OBJECT_KEY_RE = re.compile(r"(?:^|[,{])\s*[\"']?([A-Za-z_$][\w$]{0,90})[\"']?\s*:")
_BRIDGE_RE = re.compile(
    r"(?P<callee>(?:[A-Za-z_$][\w$]*\.)*(?:sendEncryptionData|callNative|sendMessageToNative))"
    r"\s*\(\s*[\"'](?P<method>[^\"']{1,160})[\"']",
    re.I,
)


def _h5_host(client: Any) -> str:
    region = str(getattr(client, "region", "fra") or "fra").lower()
    return f"https://navimow-h5-{region}.willand.com"


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _fetch(url: str, max_bytes: int) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/javascript,text/javascript,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.2-beta1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            raw = resp.read(max_bytes + 1)
            status = int(getattr(resp, "status", 200))
            content_type = str(resp.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as err:
        raw = err.read(max_bytes + 1)
        status = int(err.code)
        content_type = str(err.headers.get("Content-Type", ""))
    except urllib.error.URLError as err:
        return {
            "ok": False,
            "url": _safe_url(url),
            "transport_error": sanitize(str(err.reason)),
        }
    except Exception as err:  # noqa: BLE001 - bounded diagnostics-only discovery
        return {
            "ok": False,
            "url": _safe_url(url),
            "transport_error": sanitize(f"{type(err).__name__}: {err}"),
        }

    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    return {
        "ok": 200 <= status < 400,
        "url": _safe_url(url),
        "http_status": status,
        "content_type": content_type,
        "body_length_read": len(raw),
        "body_sha256": hashlib.sha256(raw).hexdigest(),
        "truncated": truncated,
        "_text": raw.decode("utf-8", errors="replace"),
    }


def _public(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "_text"}


def _root_scripts(html: str, base_url: str) -> list[str]:
    values: list[str] = []
    for src in _SCRIPT_RE.findall(html):
        url = urllib.parse.urljoin(base_url, src.strip())
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "https" and parsed.netloc and url not in values:
            values.append(url)
    values.sort(
        key=lambda value: (
            0
            if any(
                token in urllib.parse.urlsplit(value).path.lower()
                for token in ("app", "main", "entry", "index", "runtime")
            )
            else 1,
            len(value),
        )
    )
    return values[:_MAX_ROOT_ASSETS]


def _object_keys(text: str) -> list[str]:
    ignored = {
        "class",
        "style",
        "children",
        "props",
        "key",
        "ref",
        "type",
        "name",
        "render",
    }
    values: list[str] = []
    for key in _OBJECT_KEY_RE.findall(text):
        if key.lower() in ignored or key in values:
            continue
        values.append(key)
        if len(values) >= 60:
            break
    return values


def _bridge_calls(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for match in _BRIDGE_RE.finditer(text):
        row = {
            "callee": match.group("callee"),
            "method": match.group("method"),
        }
        if row not in rows:
            rows.append(row)
        if len(rows) >= 20:
            break
    return rows


def _context(text: str, index: int, term: str, source: str) -> dict[str, Any]:
    lo = max(0, index - _CONTEXT_RADIUS)
    hi = min(len(text), index + len(term) + _CONTEXT_RADIUS)
    raw = text[lo:hi]
    return sanitize(
        {
            "term": term,
            "source": _safe_url(source),
            "mowerbot_paths": sorted(set(_MOWERBOT_RE.findall(raw)))[:24],
            "http_methods": sorted({value.upper() for value in _HTTP_RE.findall(raw)}),
            "skip_encryption": sorted({value.lower() for value in _SKIP_RE.findall(raw)}),
            "object_keys": _object_keys(raw),
            "bridge_calls": _bridge_calls(raw),
            "context": re.sub(r"\s+", " ", raw).strip(),
        }
    )


def _target_contexts(text: str, source: str) -> list[dict[str, Any]]:
    lower = text.lower()
    rows: list[dict[str, Any]] = []
    for term in _TARGET_TERMS:
        needle = term.lower()
        start = 0
        per_term = 0
        while per_term < 6 and len(rows) < _MAX_CONTEXTS:
            index = lower.find(needle, start)
            if index < 0:
                break
            rows.append(_context(text, index, term, source))
            start = index + len(needle)
            per_term += 1
    return rows


def _request_rows(text: str, source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    lower = text.lower()
    for match in _MOWERBOT_RE.finditer(text):
        path = match.group(1)
        path_lower = path.lower()
        if path in seen:
            continue
        if not any(
            token in path_lower
            for token in ("message", "notification", "unread", "read")
        ):
            continue
        seen.add(path)
        lo = max(0, match.start() - _CONTEXT_RADIUS)
        hi = min(len(text), match.end() + _CONTEXT_RADIUS)
        raw = text[lo:hi]
        matched_terms = [term for term in _TARGET_TERMS if term.lower() in lower[lo:hi]]
        rows.append(
            sanitize(
                {
                    "path": path,
                    "source": _safe_url(source),
                    "matched_terms": matched_terms,
                    "http_methods": sorted({value.upper() for value in _HTTP_RE.findall(raw)}),
                    "skip_encryption": sorted({value.lower() for value in _SKIP_RE.findall(raw)}),
                    "object_keys": _object_keys(raw),
                    "bridge_calls": _bridge_calls(raw),
                    "context": re.sub(r"\s+", " ", raw).strip(),
                }
            )
        )
        if len(rows) >= _MAX_REQUESTS:
            break
    rows.sort(
        key=lambda row: (
            0 if "clearBatchMessageRead" in row.get("matched_terms", []) else 1,
            0 if "vehicleMessageListField" in row.get("matched_terms", []) else 1,
            str(row.get("path") or ""),
        )
    )
    return rows


def _dynamic_candidates(text: str, base_url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    lower = text.lower()
    for match in _JS_RE.finditer(text):
        url = _safe_url(urllib.parse.urljoin(base_url, match.group(1).strip()))
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or url in seen:
            continue
        lo = max(0, match.start() - _DYNAMIC_RADIUS)
        hi = min(len(text), match.end() + _DYNAMIC_RADIUS)
        nearby = lower[lo:hi]
        terms = [term for term in _THEME_TERMS if term in nearby or term in url.lower()]
        if not terms:
            continue
        seen.add(url)
        rows.append(
            {
                "url": url,
                "theme_terms": sorted(set(terms)),
                "score": sum(6 if term == "clearbatchmessageread" else 1 for term in set(terms)),
            }
        )
    rows.sort(key=lambda row: (-int(row["score"]), str(row["url"])))
    return rows[:_MAX_DYNAMIC_ASSETS]


def probe_notification_read_h5(client: Any) -> dict[str, Any]:
    """Inspect public H5 source for notification read/write request structure."""
    host = _h5_host(client)
    pages: list[dict[str, Any]] = []
    root_scripts: list[str] = []
    seen_page_hashes: set[str] = set()

    for path in _ENTRY_PATHS:
        url = urllib.parse.urljoin(host + "/", path.lstrip("/"))
        result = _fetch(url, _MAX_HTML_BYTES)
        text = str(result.get("_text") or "")
        row = _public(result)
        if text:
            scripts = _root_scripts(text, url)
            row["script_count"] = len(scripts)
            row["script_urls"] = [_safe_url(value) for value in scripts]
            if result.get("body_sha256") not in seen_page_hashes:
                root_scripts.extend(scripts)
                seen_page_hashes.add(str(result.get("body_sha256")))
        pages.append(row)

    unique_root: list[str] = []
    for url in root_scripts:
        if url not in unique_root:
            unique_root.append(url)
    unique_root = unique_root[:_MAX_ROOT_ASSETS]

    assets: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    dynamic: dict[str, dict[str, Any]] = {}

    def inspect(url: str, *, kind: str) -> None:
        result = _fetch(url, _MAX_JS_BYTES)
        text = str(result.get("_text") or "")
        row = _public(result)
        row["kind"] = kind
        if text:
            found_contexts = _target_contexts(text, url)
            found_requests = _request_rows(text, url)
            row["target_terms"] = sorted(
                {
                    str(item.get("term"))
                    for item in found_contexts
                    if item.get("term")
                }
            )
            row["request_paths"] = sorted(
                {
                    str(item.get("path"))
                    for item in found_requests
                    if item.get("path")
                }
            )
            contexts.extend(found_contexts)
            requests.extend(found_requests)
            for candidate in _dynamic_candidates(text, url):
                dynamic.setdefault(str(candidate["url"]), candidate)
        assets.append(row)

    for url in unique_root:
        inspect(url, kind="root")

    for candidate in sorted(
        dynamic.values(),
        key=lambda row: (-int(row["score"]), str(row["url"])),
    )[:_MAX_DYNAMIC_ASSETS]:
        url = str(candidate["url"])
        if any(asset.get("url") == _safe_url(url) for asset in assets):
            continue
        inspect(url, kind="dynamic")

    # Deduplicate bounded findings while preserving the highest-signal order.
    dedup_contexts: list[dict[str, Any]] = []
    seen_contexts: set[tuple[str, str, str]] = set()
    for row in contexts:
        marker = (
            str(row.get("term") or ""),
            str(row.get("source") or ""),
            str(row.get("context") or ""),
        )
        if marker in seen_contexts:
            continue
        seen_contexts.add(marker)
        dedup_contexts.append(row)
        if len(dedup_contexts) >= _MAX_CONTEXTS:
            break

    dedup_requests: list[dict[str, Any]] = []
    seen_requests: set[tuple[str, str]] = set()
    for row in requests:
        marker = (str(row.get("path") or ""), str(row.get("source") or ""))
        if marker in seen_requests:
            continue
        seen_requests.add(marker)
        dedup_requests.append(row)
        if len(dedup_requests) >= _MAX_REQUESTS:
            break

    return sanitize(
        {
            "read_only": True,
            "beta_only": True,
            "public_unauthenticated_h5_only": True,
            "normal_notification_polling_unchanged": True,
            "mutation_calls_executed": False,
            "source": "home_assistant_download",
            "host": host,
            "entry_paths": list(_ENTRY_PATHS),
            "targets": list(_TARGET_TERMS),
            "limits": {
                "timeout_seconds_per_request": _TIMEOUT_SECONDS,
                "max_html_bytes": _MAX_HTML_BYTES,
                "max_js_bytes_per_asset": _MAX_JS_BYTES,
                "max_root_assets": _MAX_ROOT_ASSETS,
                "max_dynamic_assets": _MAX_DYNAMIC_ASSETS,
                "max_contexts": _MAX_CONTEXTS,
            },
            "credential_safety": (
                "No token, cookie, uid, device id, mower serial or encrypted p:101 "
                "business payload is sent to H5. Only public GET resources are read."
            ),
            "investigation_goal": (
                "Recover clearBatchMessageRead request payload/semantics and determine "
                "whether the official app supports marking one message and all messages read."
            ),
            "pages": pages,
            "assets": assets,
            "contexts": dedup_contexts,
            "request_candidates": dedup_requests,
            "note": (
                "0.4.2-beta1 records bounded public H5 source context only. It does "
                "not call clearBatchMessageRead or any other notification mutation endpoint."
            ),
        }
    )
