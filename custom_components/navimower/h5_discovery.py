"""Read-only H5/native-bridge discovery for Home Assistant diagnostics.

Beta25 keeps beta24's bounded public H5 inspection, but follows the evidence from
real Navimow bundles more closely: minified helper aliases such as ``Rd.callNative``
and ``je.sendEncryptionData`` are parsed generically, and message/notification/
history-related lazy JavaScript chunks referenced by inspected bundles can be
followed with strict request and size limits.

No Navimow credentials, mower identifiers or p:101 payloads are sent to H5.
Fetched source bodies are never stored in diagnostics.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .diagnostics_export import sanitize

_ENTRY_PATHS: tuple[str, ...] = (
    "/message/message/list",
    "/old/",
)
_KEYWORDS: tuple[str, ...] = (
    "message",
    "notification",
    "notice",
    "push",
    "event",
    "history",
    "newmessages",
    "baseurl",
    "graphql",
    "axios",
    "sendmessagetonative",
    "messagehandlers",
    "androidandjs",
    "callnative",
    "sendencryptiondata",
)
_CONTEXT_TERMS: tuple[str, ...] = (
    "sendMessageToNative",
    "messageHandlers",
    "AndroidAndJs",
    "callNative",
    "sendEncryptionData",
    "newMessages",
    "notification",
    "notice_title",
    "noticeTitle",
    "messageHistory",
    "history",
)
_CHUNK_THEME_TERMS: tuple[str, ...] = (
    "message",
    "notification",
    "notice",
    "push",
    "history",
    "newmessages",
)
_MAX_HTML_BYTES = 256 * 1024
_MAX_JS_BYTES = 2 * 1024 * 1024
_MAX_SCRIPT_ASSETS = 6
_MAX_DYNAMIC_CHUNKS = 4
_MAX_FINDINGS = 80
_MAX_CONTEXTS = 32
_CONTEXT_RADIUS = 320
_CHUNK_CONTEXT_RADIUS = 700
_TIMEOUT_SECONDS = 5

_SCRIPT_SRC_RE = re.compile(
    r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"'][^>]*>",
    re.IGNORECASE,
)
_ABSOLUTE_URL_RE = re.compile(
    r"https?://[A-Za-z0-9._:-]+(?:/[A-Za-z0-9_./?&=%+#@~,:;!$()*+\-]{0,300})?"
)
_QUOTED_STRING_RE = re.compile(r"[\"']([^\"'\r\n]{2,300})[\"']")
_BASE_URL_RE = re.compile(
    r"(?:baseURL|baseUrl|apiBase|apiBaseUrl|baseApi)\s*[:=]\s*[\"']([^\"']{1,300})[\"']"
)
_PATH_LITERAL_RE = re.compile(r"[\"'](/[^\"'\r\n]{1,240})[\"']")
_CALL_LITERAL_RE = re.compile(
    r"(?:fetch|axios(?:\.(?:get|post|put|delete|request))?|\.(?:get|post|put|delete))"
    r"\s*\(\s*[\"']([^\"'\r\n]{1,240})[\"']",
    re.IGNORECASE,
)
_BRIDGE_OBJECT_RE = re.compile(
    r"(?:sendMessageToNative|postMessage|callNative|sendEncryptionData)\s*\("
    r"(?P<body>.{0,1200}?)(?:\)|;)",
    re.IGNORECASE | re.DOTALL,
)
_METHOD_LITERAL_RE = re.compile(
    r"(?:method|action|name)\s*:\s*[\"']([^\"']{1,120})[\"']",
    re.IGNORECASE,
)
_CALLBACK_LITERAL_RE = re.compile(
    r"callback\s*:\s*[\"']([^\"']{1,160})[\"']",
    re.IGNORECASE,
)
_DIRECT_BRIDGE_RE = re.compile(
    r"(?:[A-Za-z_$][\w$]*\s*\.\s*)*"
    r"(?:sendMessageToNative|callNative|sendEncryptionData)\s*\(\s*"
    r"[\"']([^\"']{1,120})[\"']",
    re.IGNORECASE,
)
_GENERIC_BRIDGE_CALL_RE = re.compile(
    r"(?P<callee>(?:[A-Za-z_$][\w$]*\s*\.\s*)*"
    r"(?:callNative|sendEncryptionData|sendMessageToNative))\s*\("
    r"\s*[\"'](?P<method>[^\"']{1,120})[\"']",
    re.IGNORECASE,
)
_JS_LITERAL_RE = re.compile(
    r"[\"']([^\"'\r\n]{1,360}\.js(?:\?[^\"'\r\n]{0,120})?)[\"']",
    re.IGNORECASE,
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
            "User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.1-beta25",
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
        return {"ok": False, "url": _safe_url(url), "transport_error": sanitize(str(err.reason))}
    except Exception as err:  # noqa: BLE001 - bounded diagnostics discovery
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


def _extract_script_urls(html: str, base_url: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for src in _SCRIPT_SRC_RE.findall(html):
        url = urllib.parse.urljoin(base_url, src.strip())
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _prioritize_scripts(urls: list[str]) -> list[str]:
    def score(url: str) -> tuple[int, int]:
        path = urllib.parse.urlsplit(url).path.lower()
        preferred = any(token in path for token in ("app", "main", "index", "runtime", "entry"))
        vendor = "vendor" in path
        return (0 if preferred else 1 if not vendor else 2, len(path))

    return sorted(urls, key=score)[:_MAX_SCRIPT_ASSETS]


def _interesting_path(value: str) -> bool:
    low = value.lower()
    return (
        any(token in low for token in ("message", "notification", "notice", "push", "event", "history"))
        or "/api/" in low
        or low.startswith("/api")
        or low.startswith("api/")
    )


def _bridge_findings(text: str) -> dict[str, Any]:
    methods: set[str] = set(_DIRECT_BRIDGE_RE.findall(text))
    callbacks: set[str] = set()
    bridge_objects: list[dict[str, Any]] = []
    calls: list[dict[str, str]] = []

    for match in _GENERIC_BRIDGE_CALL_RE.finditer(text):
        method = match.group("method")
        callee = re.sub(r"\s+", "", match.group("callee"))
        methods.add(method)
        row = {"callee": callee, "method": method}
        if row not in calls:
            calls.append(row)
        if len(calls) >= _MAX_FINDINGS:
            break

    for match in _BRIDGE_OBJECT_RE.finditer(text):
        body = match.group("body")
        obj_methods = _METHOD_LITERAL_RE.findall(body)
        obj_callbacks = _CALLBACK_LITERAL_RE.findall(body)
        methods.update(obj_methods)
        callbacks.update(obj_callbacks)
        if obj_methods or obj_callbacks:
            bridge_objects.append({"methods": obj_methods[:8], "callbacks": obj_callbacks[:8]})
        if len(bridge_objects) >= _MAX_FINDINGS:
            break

    for method in list(methods):
        callback = f"{method}Callback"
        if callback in text:
            callbacks.add(callback)

    return {
        "methods": sorted(methods)[:_MAX_FINDINGS],
        "callbacks": sorted(callbacks)[:_MAX_FINDINGS],
        "calls": calls[:_MAX_FINDINGS],
        "objects": bridge_objects[:_MAX_FINDINGS],
    }


def _call_literals(text: str) -> list[str]:
    values = {value.strip() for value in _CALL_LITERAL_RE.findall(text) if _interesting_path(value)}
    return sorted(values)[:_MAX_FINDINGS]


def _path_literals(text: str) -> list[str]:
    values = {value.strip() for value in _PATH_LITERAL_RE.findall(text) if _interesting_path(value)}
    return sorted(values)[:_MAX_FINDINGS]


def _contexts(text: str) -> list[dict[str, str]]:
    lower = text.lower()
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for term in _CONTEXT_TERMS:
        needle = term.lower()
        start = 0
        while len(rows) < _MAX_CONTEXTS:
            idx = lower.find(needle, start)
            if idx < 0:
                break
            lo = max(0, idx - _CONTEXT_RADIUS)
            hi = min(len(text), idx + len(term) + _CONTEXT_RADIUS)
            snippet = re.sub(r"\s+", " ", text[lo:hi]).strip()
            key = (term.lower(), snippet)
            if key not in seen:
                seen.add(key)
                rows.append({"term": term, "context": snippet})
            start = idx + len(term)
        if len(rows) >= _MAX_CONTEXTS:
            break
    return rows


def _dynamic_chunk_candidates(text: str, base_url: str) -> list[dict[str, Any]]:
    """Find JS literals whose URL or nearby source context is notification-themed."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    lower = text.lower()
    for match in _JS_LITERAL_RE.finditer(text):
        raw = match.group(1).strip()
        url = urllib.parse.urljoin(base_url, raw)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc:
            continue
        safe = _safe_url(url)
        if safe in seen:
            continue
        lo = max(0, match.start() - _CHUNK_CONTEXT_RADIUS)
        hi = min(len(text), match.end() + _CHUNK_CONTEXT_RADIUS)
        nearby = lower[lo:hi]
        url_low = safe.lower()
        terms = sorted(
            {
                term
                for term in _CHUNK_THEME_TERMS
                if term in url_low or term in nearby
            }
        )
        if not terms:
            continue
        seen.add(safe)
        rows.append(
            {
                "url": safe,
                "theme_terms": terms,
                "score": sum(3 if term in url_low else 1 for term in terms),
            }
        )
    rows.sort(key=lambda row: (-int(row["score"]), str(row["url"])))
    return rows[:_MAX_FINDINGS]


def _scan_text(text: str) -> dict[str, Any]:
    lower = text.lower()
    keyword_counts = {keyword: lower.count(keyword) for keyword in _KEYWORDS if keyword in lower}

    absolute_urls: set[str] = set()
    hosts: set[str] = set()
    for match in _ABSOLUTE_URL_RE.findall(text):
        safe = _safe_url(match)
        absolute_urls.add(safe)
        host = urllib.parse.urlsplit(safe).netloc
        if host:
            hosts.add(host)

    base_urls = {_safe_url(value) for value in _BASE_URL_RE.findall(text)}

    api_like_strings: set[str] = set()
    for value in _QUOTED_STRING_RE.findall(text):
        candidate = value.strip()
        if len(candidate) <= 240 and _interesting_path(candidate):
            api_like_strings.add(candidate)

    return {
        "keyword_counts": keyword_counts,
        "hosts": sorted(hosts)[:_MAX_FINDINGS],
        "absolute_urls": sorted(absolute_urls)[:_MAX_FINDINGS],
        "base_url_candidates": sorted(base_urls)[:_MAX_FINDINGS],
        "api_like_strings": sorted(api_like_strings)[:_MAX_FINDINGS],
        "path_literals": _path_literals(text),
        "http_call_literals": _call_literals(text),
        "native_bridge": _bridge_findings(text),
        "contexts": _contexts(text),
    }


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "_text"}


def probe_h5_frontend(client: Any) -> dict[str, Any]:
    host = _h5_host(client)
    pages: list[dict[str, Any]] = []
    all_scripts: list[str] = []
    seen_page_hashes: set[str] = set()
    merged_hosts: set[str] = set()
    merged_urls: set[str] = set()
    merged_base_urls: set[str] = set()
    merged_strings: set[str] = set()
    merged_paths: set[str] = set()
    merged_calls: set[str] = set()
    merged_methods: set[str] = set()
    merged_callbacks: set[str] = set()
    merged_bridge_calls: list[dict[str, str]] = []
    merged_contexts: list[dict[str, str]] = []
    chunk_candidates: dict[str, dict[str, Any]] = {}

    def merge(findings: dict[str, Any]) -> None:
        merged_hosts.update(findings["hosts"])
        merged_urls.update(findings["absolute_urls"])
        merged_base_urls.update(findings["base_url_candidates"])
        merged_strings.update(findings["api_like_strings"])
        merged_paths.update(findings["path_literals"])
        merged_calls.update(findings["http_call_literals"])
        merged_methods.update(findings["native_bridge"]["methods"])
        merged_callbacks.update(findings["native_bridge"]["callbacks"])
        for row in findings["native_bridge"]["calls"]:
            if row not in merged_bridge_calls and len(merged_bridge_calls) < _MAX_FINDINGS:
                merged_bridge_calls.append(row)
        for row in findings["contexts"]:
            if row not in merged_contexts and len(merged_contexts) < _MAX_CONTEXTS:
                merged_contexts.append(row)

    for path in _ENTRY_PATHS:
        url = urllib.parse.urljoin(host + "/", path.lstrip("/"))
        result = _fetch(url, _MAX_HTML_BYTES)
        text = str(result.get("_text") or "")
        row = _public_result(result)
        if text:
            findings = _scan_text(text)
            row["findings"] = findings
            merge(findings)
            scripts = _extract_script_urls(text, url)
            row["script_count"] = len(scripts)
            row["script_urls"] = [_safe_url(item) for item in scripts[:20]]
            if result.get("body_sha256") not in seen_page_hashes:
                all_scripts.extend(scripts)
                seen_page_hashes.add(str(result.get("body_sha256")))
        pages.append(row)

    unique_scripts: list[str] = []
    seen_scripts: set[str] = set()
    for url in all_scripts:
        if url in seen_scripts:
            continue
        seen_scripts.add(url)
        unique_scripts.append(url)

    assets: list[dict[str, Any]] = []
    selected_scripts = _prioritize_scripts(unique_scripts)
    for url in selected_scripts:
        result = _fetch(url, _MAX_JS_BYTES)
        text = str(result.get("_text") or "")
        row = _public_result(result)
        if text:
            findings = _scan_text(text)
            row["findings"] = findings
            merge(findings)
            candidates = _dynamic_chunk_candidates(text, url)
            row["dynamic_chunk_candidate_count"] = len(candidates)
            row["dynamic_chunk_candidates"] = candidates[:20]
            for candidate in candidates:
                current = chunk_candidates.get(str(candidate["url"]))
                if current is None or int(candidate["score"]) > int(current["score"]):
                    chunk_candidates[str(candidate["url"])] = candidate
        assets.append(row)

    ranked_chunks = sorted(
        chunk_candidates.values(),
        key=lambda row: (-int(row["score"]), str(row["url"])),
    )
    dynamic_assets: list[dict[str, Any]] = []
    selected_set = set(selected_scripts)
    for candidate in ranked_chunks:
        if len(dynamic_assets) >= _MAX_DYNAMIC_CHUNKS:
            break
        url = str(candidate["url"])
        if url in selected_set:
            continue
        result = _fetch(url, _MAX_JS_BYTES)
        text = str(result.get("_text") or "")
        row = _public_result(result)
        row["theme_terms"] = candidate["theme_terms"]
        row["score"] = candidate["score"]
        if text:
            findings = _scan_text(text)
            row["findings"] = findings
            merge(findings)
        dynamic_assets.append(row)

    return {
        "read_only": True,
        "public_unauthenticated_only": True,
        "normal_polling_unchanged": True,
        "source": "home_assistant_download",
        "host": host,
        "entry_paths": list(_ENTRY_PATHS),
        "limits": {
            "timeout_seconds_per_request": _TIMEOUT_SECONDS,
            "max_html_bytes": _MAX_HTML_BYTES,
            "max_js_bytes_per_asset": _MAX_JS_BYTES,
            "max_script_assets": _MAX_SCRIPT_ASSETS,
            "max_dynamic_chunks": _MAX_DYNAMIC_CHUNKS,
            "max_findings_per_category": _MAX_FINDINGS,
            "max_contexts": _MAX_CONTEXTS,
            "context_radius_chars": _CONTEXT_RADIUS,
            "chunk_context_radius_chars": _CHUNK_CONTEXT_RADIUS,
        },
        "credential_safety": (
            "H5 discovery sends no uid, access token, device id, vehicle serial "
            "or encrypted p:101 business payload. Source bodies are not stored."
        ),
        "page_count": len(pages),
        "discovered_script_count": len(unique_scripts),
        "inspected_script_count": len(assets),
        "dynamic_chunk_candidate_count": len(ranked_chunks),
        "inspected_dynamic_chunk_count": len(dynamic_assets),
        "summary": {
            "hosts": sorted(merged_hosts)[:_MAX_FINDINGS],
            "absolute_urls": sorted(merged_urls)[:_MAX_FINDINGS],
            "base_url_candidates": sorted(merged_base_urls)[:_MAX_FINDINGS],
            "api_like_strings": sorted(merged_strings)[:_MAX_FINDINGS],
            "path_literals": sorted(merged_paths)[:_MAX_FINDINGS],
            "http_call_literals": sorted(merged_calls)[:_MAX_FINDINGS],
            "native_bridge_methods": sorted(merged_methods)[:_MAX_FINDINGS],
            "native_bridge_callbacks": sorted(merged_callbacks)[:_MAX_FINDINGS],
            "native_bridge_calls": merged_bridge_calls[:_MAX_FINDINGS],
            "dynamic_chunk_candidates": ranked_chunks[:20],
            "contexts": merged_contexts[:_MAX_CONTEXTS],
        },
        "pages": pages,
        "assets": assets,
        "dynamic_assets": dynamic_assets,
        "note": (
            "Beta25 recognizes aliased callNative/sendEncryptionData bridge calls and "
            "follows only bounded message/notification/history-themed lazy JS chunks."
        ),
    }
