"""Read-only public-H5 discovery for notification read-state mutations.

0.4.2-beta1 uses this module only from Home Assistant Download diagnostics. It
fetches bounded public Navimow H5 HTML/JavaScript and records source context
around notification read-state routes. It never sends Navimow credentials,
mower identity or encrypted business payloads, and it never calls a mutation
endpoint.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .diagnostics_sanitize import sanitize

_ENTRY_PATHS = ("/message/message/list", "/old/")
_TARGET_TERMS = (
    "clearBatchMessageRead",
    "vehicleMessageListField",
    "queryUnreadRedCountForVehicle",
    "getUnreadMessageAndRedCount",
    "get-vehicle-jump-target",
)
_THEME_TERMS = (
    "message",
    "notification",
    "messagecenter",
    "messagelist",
    "unread",
    "clearbatchmessageread",
)
_MAX_HTML_BYTES = 256 * 1024
_MAX_JS_BYTES = 2 * 1024 * 1024
_MAX_ROOT_ASSETS = 8
_MAX_DYNAMIC_ASSETS = 10
_MAX_CONTEXTS = 36
_MAX_REQUESTS = 48
_CONTEXT_RADIUS = 5000
_DYNAMIC_RADIUS = 3000
_TIMEOUT_SECONDS = 5

_SCRIPT_RE = re.compile(r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
_JS_RE = re.compile(r"[\"']([^\"'\r\n]{1,420}\.js(?:\?[^\"'\r\n]{0,120})?)[\"']", re.I)
_MOWERBOT_RE = re.compile(r"[\"'](/mowerbot/[^\"'\r\n]{1,280})[\"']", re.I)
_HTTP_RE = re.compile(r"method\s*:\s*[\"']?(GET|POST|PUT|DELETE|PATCH)[\"']?", re.I)
_SKIP_RE = re.compile(r"skipEncryption\s*:\s*(true|false)", re.I)
_OBJECT_KEY_RE = re.compile(r"(?:^|[,{])\s*[\"']?([A-Za-z_$][\w$]{0,90})[\"']?\s*:")
_BRIDGE_RE = re.compile(
    r"(?P<callee>(?:[A-Za-z_$][\w$]*\.)*"
    r"(?:sendEncryptionData|callNative|sendMessageToNative))"
    r"\s*\(\s*[\"'](?P<method>[^\"']{1,160})[\"']",
    re.I,
)


def _host(client: Any) -> str:
    region = str(getattr(client, "region", "fra") or "fra").lower()
    return f"https://navimow-h5-{region}.willand.com"


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _fetch(url: str, max_bytes: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/javascript,text/javascript,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.2-beta1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read(max_bytes + 1)
            status = int(getattr(response, "status", 200))
            content_type = str(response.headers.get("Content-Type", ""))
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
    except Exception as err:  # noqa: BLE001 - bounded optional diagnostics
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


def _public(fetch_result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fetch_result.items() if key != "_text"}


def _script_urls(html: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for value in _SCRIPT_RE.findall(html):
        url = urllib.parse.urljoin(base_url, value.strip())
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "https" and parsed.netloc and url not in urls:
            urls.append(url)
    urls.sort(
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
    return urls[:_MAX_ROOT_ASSETS]


def _object_keys(text: str) -> list[str]:
    ignored = {"class", "style", "children", "props", "key", "ref", "render"}
    keys: list[str] = []
    for key in _OBJECT_KEY_RE.findall(text):
        if key.lower() in ignored or key in keys:
            continue
        keys.append(key)
        if len(keys) >= 64:
            break
    return keys


def _bridge_calls(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for match in _BRIDGE_RE.finditer(text):
        row = {"callee": match.group("callee"), "method": match.group("method")}
        if row not in rows:
            rows.append(row)
        if len(rows) >= 24:
            break
    return rows


def _request_structure(text: str) -> dict[str, Any]:
    return {
        "mowerbot_paths": sorted(set(_MOWERBOT_RE.findall(text)))[:32],
        "http_methods": sorted({value.upper() for value in _HTTP_RE.findall(text)}),
        "skip_encryption": sorted({value.lower() for value in _SKIP_RE.findall(text)}),
        "object_keys": _object_keys(text),
        "bridge_calls": _bridge_calls(text),
    }


def _contexts(text: str, source: str) -> list[dict[str, Any]]:
    lower = text.lower()
    rows: list[dict[str, Any]] = []
    for term in _TARGET_TERMS:
        start = 0
        found_for_term = 0
        needle = term.lower()
        while found_for_term < 6 and len(rows) < _MAX_CONTEXTS:
            index = lower.find(needle, start)
            if index < 0:
                break
            lo = max(0, index - _CONTEXT_RADIUS)
            hi = min(len(text), index + len(term) + _CONTEXT_RADIUS)
            nearby = text[lo:hi]
            rows.append(
                {
                    "term": term,
                    "source": _safe_url(source),
                    **_request_structure(nearby),
                    # Source is public unauthenticated H5 code. Keep this bounded
                    # context verbatim (whitespace-normalized) so payload syntax
                    # is not destroyed by generic URL sanitization.
                    "context": re.sub(r"\s+", " ", nearby).strip(),
                }
            )
            start = index + len(needle)
            found_for_term += 1
    return rows


def _request_candidates(text: str, source: str) -> list[dict[str, Any]]:
    lower = text.lower()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _MOWERBOT_RE.finditer(text):
        path = match.group(1)
        if path in seen:
            continue
        path_lower = path.lower()
        if not any(token in path_lower for token in ("message", "unread", "read")):
            continue
        seen.add(path)
        lo = max(0, match.start() - _CONTEXT_RADIUS)
        hi = min(len(text), match.end() + _CONTEXT_RADIUS)
        nearby = text[lo:hi]
        matched = [term for term in _TARGET_TERMS if term.lower() in lower[lo:hi]]
        rows.append(
            {
                "path": path,
                "source": _safe_url(source),
                "matched_terms": matched,
                **_request_structure(nearby),
                "context": re.sub(r"\s+", " ", nearby).strip(),
            }
        )
        if len(rows) >= _MAX_REQUESTS:
            break
    rows.sort(
        key=lambda row: (
            0 if "clearBatchMessageRead" in row["matched_terms"] else 1,
            0 if "vehicleMessageListField" in row["matched_terms"] else 1,
            str(row["path"]),
        )
    )
    return rows


def _dynamic_candidates(text: str, base_url: str) -> list[dict[str, Any]]:
    lower = text.lower()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _JS_RE.finditer(text):
        url = _safe_url(urllib.parse.urljoin(base_url, match.group(1).strip()))
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or url in seen:
            continue
        lo = max(0, match.start() - _DYNAMIC_RADIUS)
        hi = min(len(text), match.end() + _DYNAMIC_RADIUS)
        nearby = lower[lo:hi]
        terms = sorted(
            {
                term
                for term in _THEME_TERMS
                if term in nearby or term in url.lower()
            }
        )
        if not terms:
            continue
        seen.add(url)
        rows.append(
            {
                "url": url,
                "theme_terms": terms,
                "score": sum(
                    8 if term == "clearbatchmessageread" else 1 for term in terms
                ),
            }
        )
    rows.sort(key=lambda row: (-int(row["score"]), str(row["url"])))
    return rows[:_MAX_DYNAMIC_ASSETS]


def probe_notification_read_h5(client: Any) -> dict[str, Any]:
    """Inspect public H5 source for notification read request structure."""
    host = _host(client)
    pages: list[dict[str, Any]] = []
    root_scripts: list[str] = []
    seen_page_hashes: set[str] = set()

    for entry_path in _ENTRY_PATHS:
        url = urllib.parse.urljoin(host + "/", entry_path.lstrip("/"))
        result = _fetch(url, _MAX_HTML_BYTES)
        text = str(result.get("_text") or "")
        row = _public(result)
        if text:
            scripts = _script_urls(text, url)
            row["script_count"] = len(scripts)
            row["script_urls"] = [_safe_url(item) for item in scripts]
            page_hash = str(result.get("body_sha256") or "")
            if page_hash not in seen_page_hashes:
                root_scripts.extend(scripts)
                seen_page_hashes.add(page_hash)
        pages.append(row)

    unique_root: list[str] = []
    for url in root_scripts:
        if url not in unique_root:
            unique_root.append(url)
    unique_root = unique_root[:_MAX_ROOT_ASSETS]

    assets: list[dict[str, Any]] = []
    all_contexts: list[dict[str, Any]] = []
    all_requests: list[dict[str, Any]] = []
    dynamic: dict[str, dict[str, Any]] = {}

    def inspect(url: str, kind: str) -> None:
        result = _fetch(url, _MAX_JS_BYTES)
        text = str(result.get("_text") or "")
        row = _public(result)
        row["kind"] = kind
        if text:
            contexts = _contexts(text, url)
            requests = _request_candidates(text, url)
            row["target_terms"] = sorted(
                {str(item["term"]) for item in contexts if item.get("term")}
            )
            row["request_paths"] = sorted(
                {str(item["path"]) for item in requests if item.get("path")}
            )
            all_contexts.extend(contexts)
            all_requests.extend(requests)
            for candidate in _dynamic_candidates(text, url):
                dynamic.setdefault(str(candidate["url"]), candidate)
        assets.append(row)

    for url in unique_root:
        inspect(url, "root")

    selected_dynamic = sorted(
        dynamic.values(),
        key=lambda row: (-int(row["score"]), str(row["url"])),
    )[:_MAX_DYNAMIC_ASSETS]
    for candidate in selected_dynamic:
        url = str(candidate["url"])
        if any(asset.get("url") == _safe_url(url) for asset in assets):
            continue
        inspect(url, "dynamic")

    contexts: list[dict[str, Any]] = []
    seen_contexts: set[tuple[str, str, str]] = set()
    for row in all_contexts:
        marker = (str(row["term"]), str(row["source"]), str(row["context"]))
        if marker in seen_contexts:
            continue
        seen_contexts.add(marker)
        contexts.append(row)
        if len(contexts) >= _MAX_CONTEXTS:
            break

    requests: list[dict[str, Any]] = []
    seen_requests: set[tuple[str, str]] = set()
    for row in all_requests:
        marker = (str(row["path"]), str(row["source"]))
        if marker in seen_requests:
            continue
        seen_requests.add(marker)
        requests.append(row)
        if len(requests) >= _MAX_REQUESTS:
            break

    return {
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
        "contexts": contexts,
        "request_candidates": requests,
        "note": (
            "0.4.2-beta1 records bounded public H5 source context only. It does "
            "not call clearBatchMessageRead or any other notification mutation endpoint."
        ),
    }
