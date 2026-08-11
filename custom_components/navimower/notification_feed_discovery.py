"""Targeted public-H5 discovery for the main Navimow Notification feed.

Beta27 follows the evidence captured from the real app UI. Instead of broad
message endpoint guessing, it searches public H5 JavaScript specifically around
strings rendered by the main Notification screen (All / Important / Work
status / System / Device / newMessages / No more messages / Failed to load new
messages). It then records only nearby request structure: mowerbot paths, HTTP
method literals and native/encryption bridge calls.

No credentials, mower serials, account ids or p:101 payloads are sent. Source
HTML/JavaScript bodies are never persisted in diagnostics.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .diagnostics_export import sanitize

_ANCHORS: tuple[str, ...] = (
    "All",
    "Important",
    "Work status",
    "System",
    "Device",
    "newMessages",
    "No more messages",
    "Failed to load new messages",
)
_ENTRY_PATHS: tuple[str, ...] = ("/message/message/list", "/old/")
_MAX_HTML_BYTES = 256 * 1024
_MAX_JS_BYTES = 2 * 1024 * 1024
_MAX_ROOT_ASSETS = 6
_MAX_DYNAMIC_ASSETS = 6
_MAX_CONTEXTS = 40
_CONTEXT_RADIUS = 1800
_DYNAMIC_RADIUS = 2200
_TIMEOUT_SECONDS = 5

_SCRIPT_RE = re.compile(r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
_JS_LITERAL_RE = re.compile(r"[\"']([^\"'\r\n]{1,360}\.js(?:\?[^\"'\r\n]{0,120})?)[\"']", re.I)
_MOWERBOT_RE = re.compile(r"[\"'](/mowerbot/[^\"'\r\n]{1,240})[\"']", re.I)
_HTTP_METHOD_RE = re.compile(r"(?:method\s*:\s*|\.)[\"']?(GET|POST|PUT|DELETE|PATCH)[\"']?", re.I)
_SEND_ENCRYPT_RE = re.compile(
    r"(?P<callee>(?:[A-Za-z_$][\w$]*\.)*(?:sendEncryptionData|callNative|sendMessageToNative))"
    r"\s*\(\s*[\"'](?P<method>[^\"']{1,140})[\"']",
    re.I,
)
_REQUEST_HINT_RE = re.compile(
    r"(?:skipEncryption|sendEncryptionData|callNative|axios|fetch|\.post\(|\.get\(|method\s*:)",
    re.I,
)


def _host(client: Any) -> str:
    region = str(getattr(client, "region", "fra") or "fra").lower()
    return f"https://navimow-h5-{region}.willand.com"


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _fetch(url: str, limit: int) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/javascript,text/javascript,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.1-beta27",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            raw = resp.read(limit + 1)
            status = int(getattr(resp, "status", 200))
            content_type = str(resp.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as err:
        raw = err.read(limit + 1)
        status = int(err.code)
        content_type = str(err.headers.get("Content-Type", ""))
    except urllib.error.URLError as err:
        return {"ok": False, "url": _safe_url(url), "transport_error": sanitize(str(err.reason))}
    except Exception as err:  # noqa: BLE001 - bounded diagnostics-only discovery
        return {
            "ok": False,
            "url": _safe_url(url),
            "transport_error": sanitize(f"{type(err).__name__}: {err}"),
        }

    truncated = len(raw) > limit
    raw = raw[:limit]
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
    out: list[str] = []
    seen: set[str] = set()
    for src in _SCRIPT_RE.findall(html):
        url = urllib.parse.urljoin(base_url, src.strip())
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or url in seen:
            continue
        seen.add(url)
        out.append(url)
    out.sort(key=lambda url: (0 if any(t in url.lower() for t in ("app", "main", "entry", "index")) else 1, len(url)))
    return out[:_MAX_ROOT_ASSETS]


def _anchor_contexts(text: str, source_url: str) -> list[dict[str, Any]]:
    lower = text.lower()
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for anchor in _ANCHORS:
        needle = anchor.lower()
        start = 0
        while len(rows) < _MAX_CONTEXTS:
            idx = lower.find(needle, start)
            if idx < 0:
                break
            bucket = idx // 500
            key = (needle, bucket)
            start = idx + max(1, len(needle))
            if key in seen:
                continue
            seen.add(key)
            lo = max(0, idx - _CONTEXT_RADIUS)
            hi = min(len(text), idx + len(anchor) + _CONTEXT_RADIUS)
            snippet = re.sub(r"\s+", " ", text[lo:hi]).strip()
            routes = sorted(set(_MOWERBOT_RE.findall(snippet)))
            methods = sorted({value.upper() for value in _HTTP_METHOD_RE.findall(snippet)})
            bridges = []
            for match in _SEND_ENCRYPT_RE.finditer(snippet):
                row = {
                    "callee": match.group("callee"),
                    "method": match.group("method"),
                }
                if row not in bridges:
                    bridges.append(row)
            rows.append(
                {
                    "anchor": anchor,
                    "source": _safe_url(source_url),
                    "mowerbot_paths": routes[:20],
                    "http_methods": methods,
                    "bridge_calls": bridges[:20],
                    "has_request_hint": bool(_REQUEST_HINT_RE.search(snippet)),
                    "context": snippet,
                }
            )
        if len(rows) >= _MAX_CONTEXTS:
            break
    return rows


def _dynamic_candidates(text: str, base_url: str) -> list[dict[str, Any]]:
    lower = text.lower()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _JS_LITERAL_RE.finditer(text):
        url = urllib.parse.urljoin(base_url, match.group(1).strip())
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc:
            continue
        safe = _safe_url(url)
        if safe in seen:
            continue
        lo = max(0, match.start() - _DYNAMIC_RADIUS)
        hi = min(len(text), match.end() + _DYNAMIC_RADIUS)
        nearby = lower[lo:hi]
        matched = [anchor for anchor in _ANCHORS if anchor.lower() in nearby]
        if not matched:
            continue
        seen.add(safe)
        rows.append({"url": safe, "anchors": matched, "score": len(matched)})
    rows.sort(key=lambda row: (-int(row["score"]), str(row["url"])))
    return rows


def probe_main_notification_feed(client: Any) -> dict[str, Any]:
    """Discover request clues specifically around the main Notification UI."""
    host = _host(client)
    pages: list[dict[str, Any]] = []
    scripts: list[str] = []
    page_hashes: set[str] = set()

    for path in _ENTRY_PATHS:
        url = urllib.parse.urljoin(host + "/", path.lstrip("/"))
        result = _fetch(url, _MAX_HTML_BYTES)
        text = str(result.get("_text") or "")
        row = _public(result)
        if text:
            found = _root_scripts(text, url)
            row["script_urls"] = [_safe_url(item) for item in found]
            row["anchor_contexts"] = _anchor_contexts(text, url)
            body_hash = str(result.get("body_sha256") or "")
            if body_hash not in page_hashes:
                scripts.extend(found)
                page_hashes.add(body_hash)
        pages.append(row)

    unique_scripts: list[str] = []
    seen_scripts: set[str] = set()
    for url in scripts:
        if url not in seen_scripts:
            seen_scripts.add(url)
            unique_scripts.append(url)

    root_assets: list[dict[str, Any]] = []
    candidates: dict[str, dict[str, Any]] = {}
    all_contexts: list[dict[str, Any]] = []
    for url in unique_scripts[:_MAX_ROOT_ASSETS]:
        result = _fetch(url, _MAX_JS_BYTES)
        text = str(result.get("_text") or "")
        row = _public(result)
        if text:
            contexts = _anchor_contexts(text, url)
            row["anchor_contexts"] = contexts
            all_contexts.extend(contexts)
            dynamic = _dynamic_candidates(text, url)
            row["dynamic_candidates"] = dynamic[:20]
            for candidate in dynamic:
                current = candidates.get(str(candidate["url"]))
                if current is None or int(candidate["score"]) > int(current["score"]):
                    candidates[str(candidate["url"])] = candidate
        root_assets.append(row)

    ranked = sorted(candidates.values(), key=lambda row: (-int(row["score"]), str(row["url"])))
    dynamic_assets: list[dict[str, Any]] = []
    for candidate in ranked[:_MAX_DYNAMIC_ASSETS]:
        url = str(candidate["url"])
        result = _fetch(url, _MAX_JS_BYTES)
        text = str(result.get("_text") or "")
        row = _public(result)
        row["candidate_anchors"] = candidate["anchors"]
        if text:
            contexts = _anchor_contexts(text, url)
            row["anchor_contexts"] = contexts
            all_contexts.extend(contexts)
        dynamic_assets.append(row)

    route_hits: set[str] = set()
    method_hits: set[str] = set()
    bridge_hits: list[dict[str, str]] = []
    anchor_hit_counts: dict[str, int] = {}
    request_contexts: list[dict[str, Any]] = []
    for row in all_contexts:
        anchor = str(row.get("anchor") or "")
        anchor_hit_counts[anchor] = anchor_hit_counts.get(anchor, 0) + 1
        route_hits.update(row.get("mowerbot_paths") or [])
        method_hits.update(row.get("http_methods") or [])
        for bridge in row.get("bridge_calls") or []:
            if bridge not in bridge_hits:
                bridge_hits.append(bridge)
        if row.get("has_request_hint") or row.get("mowerbot_paths") or row.get("bridge_calls"):
            request_contexts.append(row)

    return {
        "read_only": True,
        "public_unauthenticated_only": True,
        "normal_polling_unchanged": True,
        "source": "home_assistant_download",
        "host": host,
        "anchors": list(_ANCHORS),
        "limits": {
            "timeout_seconds_per_request": _TIMEOUT_SECONDS,
            "max_root_assets": _MAX_ROOT_ASSETS,
            "max_dynamic_assets": _MAX_DYNAMIC_ASSETS,
            "max_contexts": _MAX_CONTEXTS,
            "context_radius_chars": _CONTEXT_RADIUS,
        },
        "credential_safety": (
            "Public H5 discovery sends no uid, token, mower serial, device id or p:101 payload; source bodies are not stored."
        ),
        "summary": {
            "anchor_hit_counts": anchor_hit_counts,
            "mowerbot_paths": sorted(route_hits)[:80],
            "http_methods": sorted(method_hits),
            "bridge_calls": bridge_hits[:80],
            "dynamic_candidates": ranked[:30],
            "request_contexts": request_contexts[:_MAX_CONTEXTS],
        },
        "pages": pages,
        "root_assets": root_assets,
        "dynamic_assets": dynamic_assets,
        "note": (
            "Beta27 targets the main Notification UI strings and captures only nearby request structure instead of guessing endpoints."
        ),
    }
