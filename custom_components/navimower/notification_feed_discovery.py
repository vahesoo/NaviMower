"""Targeted public-H5 discovery for the main Navimow Notification feed.

Beta28 fixes the main weakness observed in beta27: generic UI words such as
``All`` can occur hundreds of times and must not decide which lazy chunks are
inspected.  Discovery now works in two stages:

1. map exact Notification UI translations to their nearby translation keys;
2. use those keys plus high-signal Notification phrases to find the owning
   JavaScript chunks, then inventory request structure inside those chunks.

The scanner records bounded source context, literal ``/mowerbot/...`` routes,
HTTP method hints, ``skipEncryption`` values, object/payload keys and native or
encryption bridge calls.  Generic ``All`` / ``System`` / ``Device`` hits remain
visible for evidence but cannot dominate chunk ranking.

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
_STRONG_ANCHORS: tuple[str, ...] = (
    "Important",
    "Work status",
    "newMessages",
    "No more messages",
    "Failed to load new messages",
)
_WEAK_ANCHORS: tuple[str, ...] = ("System", "Device")
_GENERIC_ANCHORS: tuple[str, ...] = ("All",)
_SEED_KEYS: tuple[str, ...] = ("newMessages", "messageCenter")
_ENTRY_PATHS: tuple[str, ...] = ("/message/message/list", "/old/")
_MAX_HTML_BYTES = 256 * 1024
_MAX_JS_BYTES = 2 * 1024 * 1024
_MAX_ROOT_ASSETS = 6
_MAX_DYNAMIC_ASSETS = 6
_MAX_CONTEXTS_PER_TERM = 4
_MAX_REQUEST_CONTEXTS = 48
_MAX_TRANSLATION_ROWS = 80
_MAX_REQUEST_ROWS = 60
_CONTEXT_RADIUS = 2200
_REQUEST_RADIUS = 3200
_DYNAMIC_RADIUS = 2600
_TIMEOUT_SECONDS = 5

_SCRIPT_RE = re.compile(r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
_JS_LITERAL_RE = re.compile(r"[\"']([^\"'\r\n]{1,360}\.js(?:\?[^\"'\r\n]{0,120})?)[\"']", re.I)
_MOWERBOT_RE = re.compile(r"[\"'](/mowerbot/[^\"'\r\n]{1,240})[\"']", re.I)
_HTTP_METHOD_RE = re.compile(
    r"(?:method\s*:\s*[\"']?|\.(?:request|ajax)\([^)]{0,300}?method\s*:\s*[\"']?)"
    r"(GET|POST|PUT|DELETE|PATCH)[\"']?",
    re.I,
)
_SKIP_ENCRYPTION_RE = re.compile(r"skipEncryption\s*:\s*(true|false)", re.I)
_SEND_ENCRYPT_RE = re.compile(
    r"(?P<callee>(?:[A-Za-z_$][\w$]*\.)*(?:sendEncryptionData|callNative|sendMessageToNative))"
    r"\s*\(\s*[\"'](?P<method>[^\"']{1,140})[\"']",
    re.I,
)
_REQUEST_HINT_RE = re.compile(
    r"(?:skipEncryption|sendEncryptionData|callNative|sendMessageToNative|axios|fetch|"
    r"\.post\(|\.get\(|\.request\(|method\s*:|/mowerbot/)",
    re.I,
)
_OBJECT_KEY_RE = re.compile(r"(?:^|[,{])\s*[\"']?([A-Za-z_$][\w$]{1,80})[\"']?\s*:")
_PARENT_OBJECT_RE = re.compile(r"([A-Za-z_$][\w$]{0,80})\s*:\s*\{[^{}]{0,220}$")


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
            "User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.1-beta28",
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
    out.sort(
        key=lambda url: (
            0
            if any(token in url.lower() for token in ("app", "main", "entry", "index"))
            else 1,
            len(url),
        )
    )
    return out[:_MAX_ROOT_ASSETS]


def _anchor_counts(text: str) -> dict[str, int]:
    lower = text.lower()
    return {
        anchor: lower.count(anchor.lower())
        for anchor in _ANCHORS
        if anchor.lower() in lower
    }


def _qualified_key(text: str, start: int, key: str) -> str:
    prefix = text[max(0, start - 240):start]
    parents = list(_PARENT_OBJECT_RE.finditer(prefix))
    if parents:
        parent = parents[-1].group(1)
        if parent and parent != key:
            return f"{parent}.{key}"
    return key


def _translation_keys(text: str, source_url: str) -> list[dict[str, str]]:
    """Map exact visible English strings to nearby translation object keys."""
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for anchor in _ANCHORS:
        escaped = re.escape(anchor)
        patterns = (
            re.compile(
                rf"(?P<key>[A-Za-z_$][\w$.-]{{0,100}})\s*:\s*[\"']{escaped}[\"']",
                re.I,
            ),
            re.compile(
                rf"[\"'](?P<key>[^\"'\r\n]{{1,120}})[\"']\s*:\s*[\"']{escaped}[\"']",
                re.I,
            ),
        )
        for pattern in patterns:
            for match in pattern.finditer(text):
                key = str(match.group("key")).strip()
                if not key or len(key) > 120:
                    continue
                qualified = _qualified_key(text, match.start("key"), key)
                dedupe = (anchor.lower(), qualified.lower())
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                lo = max(0, match.start() - 260)
                hi = min(len(text), match.end() + 260)
                rows.append(
                    {
                        "anchor": anchor,
                        "key": key,
                        "qualified_key": qualified,
                        "source": _safe_url(source_url),
                        "context": re.sub(r"\s+", " ", text[lo:hi]).strip(),
                    }
                )
                if len(rows) >= _MAX_TRANSLATION_ROWS:
                    return rows
    return rows


def _key_terms(translation_rows: list[dict[str, str]]) -> list[str]:
    values: set[str] = set(_SEED_KEYS)
    for row in translation_rows:
        for field in ("key", "qualified_key"):
            value = str(row.get(field) or "").strip()
            if len(value) >= 3:
                values.add(value)
    return sorted(values, key=lambda value: (-len(value), value.lower()))


def _bridge_calls(snippet: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for match in _SEND_ENCRYPT_RE.finditer(snippet):
        row = {"callee": match.group("callee"), "method": match.group("method")}
        if row not in rows:
            rows.append(row)
        if len(rows) >= 20:
            break
    return rows


def _object_keys(snippet: str) -> list[str]:
    ignored = {
        "class",
        "style",
        "children",
        "props",
        "key",
        "ref",
        "type",
        "name",
        "component",
        "render",
    }
    out: list[str] = []
    for value in _OBJECT_KEY_RE.findall(snippet):
        if value.lower() in ignored or value in out:
            continue
        out.append(value)
        if len(out) >= 40:
            break
    return out


def _context_row(
    text: str,
    idx: int,
    term: str,
    kind: str,
    source_url: str,
) -> dict[str, Any]:
    lo = max(0, idx - _CONTEXT_RADIUS)
    hi = min(len(text), idx + len(term) + _CONTEXT_RADIUS)
    snippet = re.sub(r"\s+", " ", text[lo:hi]).strip()
    return {
        "term": term,
        "kind": kind,
        "source": _safe_url(source_url),
        "mowerbot_paths": sorted(set(_MOWERBOT_RE.findall(snippet)))[:20],
        "http_methods": sorted({value.upper() for value in _HTTP_METHOD_RE.findall(snippet)}),
        "skip_encryption": sorted({value.lower() for value in _SKIP_ENCRYPTION_RE.findall(snippet)}),
        "bridge_calls": _bridge_calls(snippet),
        "object_keys": _object_keys(snippet),
        "has_request_hint": bool(_REQUEST_HINT_RE.search(snippet)),
        "context": snippet,
    }


def _target_contexts(
    text: str,
    source_url: str,
    key_terms: list[str],
) -> list[dict[str, Any]]:
    """Keep a separate bounded quota for every useful anchor/key term."""
    lower = text.lower()
    rows: list[dict[str, Any]] = []
    terms: list[tuple[str, str, int]] = []
    terms.extend((anchor, "anchor", _MAX_CONTEXTS_PER_TERM) for anchor in _STRONG_ANCHORS)
    terms.extend((anchor, "anchor", 2) for anchor in _WEAK_ANCHORS)
    # ``All`` is counted elsewhere but intentionally omitted from request context.
    terms.extend((key, "translation_key", _MAX_CONTEXTS_PER_TERM) for key in key_terms)

    seen: set[tuple[str, str, int]] = set()
    for term, kind, limit in terms:
        needle = term.lower()
        if not needle:
            continue
        start = 0
        kept = 0
        while kept < limit and len(rows) < _MAX_REQUEST_CONTEXTS:
            idx = lower.find(needle, start)
            if idx < 0:
                break
            start = idx + max(1, len(needle))
            bucket = idx // 500
            marker = (kind, needle, bucket)
            if marker in seen:
                continue
            seen.add(marker)
            rows.append(_context_row(text, idx, term, kind, source_url))
            kept += 1
        if len(rows) >= _MAX_REQUEST_CONTEXTS:
            break
    return rows


def _matched_terms(nearby: str, key_terms: list[str]) -> tuple[list[str], list[str], list[str], int]:
    low = nearby.lower()
    strong = [anchor for anchor in _STRONG_ANCHORS if anchor.lower() in low]
    weak = [anchor for anchor in _WEAK_ANCHORS if anchor.lower() in low]
    keys = [key for key in key_terms if key.lower() in low]
    theme_terms = [
        term
        for term in ("messagecenter", "notification", "newmessages", "messagehistory")
        if term in low
    ]
    score = len(strong) * 8 + len(keys) * 10 + len(theme_terms) * 3 + len(weak)
    return strong + weak, keys, theme_terms, score


def _dynamic_candidates(
    text: str,
    base_url: str,
    key_terms: list[str],
) -> list[dict[str, Any]]:
    """Rank chunks by strong phrases/translation keys; generic All scores zero."""
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
        nearby = text[lo:hi]
        anchors, keys, themes, score = _matched_terms(nearby, key_terms)
        if score < 3:
            continue
        seen.add(safe)
        rows.append(
            {
                "url": safe,
                "anchors": anchors,
                "translation_keys": keys[:20],
                "theme_terms": themes,
                "score": score,
            }
        )
    rows.sort(key=lambda row: (-int(row["score"]), str(row["url"])))
    return rows


def _request_rows(
    text: str,
    source_url: str,
    key_terms: list[str],
) -> list[dict[str, Any]]:
    """Inventory literal mowerbot requests in a selected target chunk."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for match in _MOWERBOT_RE.finditer(text):
        route = match.group(1)
        bucket = match.start() // 1000
        marker = (route, bucket)
        if marker in seen:
            continue
        seen.add(marker)
        lo = max(0, match.start() - _REQUEST_RADIUS)
        hi = min(len(text), match.end() + _REQUEST_RADIUS)
        raw = text[lo:hi]
        snippet = re.sub(r"\s+", " ", raw).strip()
        anchors, keys, themes, score = _matched_terms(raw, key_terms)
        rows.append(
            {
                "path": route,
                "source": _safe_url(source_url),
                "target_score": score,
                "matched_anchors": anchors,
                "matched_translation_keys": keys[:20],
                "theme_terms": themes,
                "http_methods": sorted({value.upper() for value in _HTTP_METHOD_RE.findall(raw)}),
                "skip_encryption": sorted({value.lower() for value in _SKIP_ENCRYPTION_RE.findall(raw)}),
                "bridge_calls": _bridge_calls(raw),
                "object_keys": _object_keys(raw),
                "context": snippet,
            }
        )
        if len(rows) >= _MAX_REQUEST_ROWS:
            break
    rows.sort(key=lambda row: (-int(row["target_score"]), str(row["path"])))
    return rows


def probe_main_notification_feed(client: Any) -> dict[str, Any]:
    """Discover the real main Notification feed request from public H5 source."""
    host = _host(client)
    pages: list[dict[str, Any]] = []
    scripts: list[str] = []
    page_hashes: set[str] = set()
    translation_rows: list[dict[str, str]] = []
    anchor_hit_counts: dict[str, int] = {}

    def merge_anchor_counts(counts: dict[str, int]) -> None:
        for key, value in counts.items():
            anchor_hit_counts[key] = anchor_hit_counts.get(key, 0) + int(value)

    def merge_translation(rows: list[dict[str, str]]) -> None:
        existing = {
            (str(row.get("anchor") or "").lower(), str(row.get("qualified_key") or "").lower())
            for row in translation_rows
        }
        for row in rows:
            marker = (row["anchor"].lower(), row["qualified_key"].lower())
            if marker not in existing and len(translation_rows) < _MAX_TRANSLATION_ROWS:
                translation_rows.append(row)
                existing.add(marker)

    for path in _ENTRY_PATHS:
        url = urllib.parse.urljoin(host + "/", path.lstrip("/"))
        result = _fetch(url, _MAX_HTML_BYTES)
        text = str(result.get("_text") or "")
        row = _public(result)
        if text:
            found = _root_scripts(text, url)
            row["script_urls"] = [_safe_url(item) for item in found]
            row["anchor_hit_counts"] = _anchor_counts(text)
            merge_anchor_counts(row["anchor_hit_counts"])
            found_keys = _translation_keys(text, url)
            row["translation_keys"] = found_keys
            merge_translation(found_keys)
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

    # Fetch roots first so translation discovery is complete before chunk ranking.
    root_sources: list[tuple[str, str]] = []
    root_assets: list[dict[str, Any]] = []
    for url in unique_scripts[:_MAX_ROOT_ASSETS]:
        result = _fetch(url, _MAX_JS_BYTES)
        text = str(result.get("_text") or "")
        row = _public(result)
        if text:
            root_sources.append((url, text))
            row["anchor_hit_counts"] = _anchor_counts(text)
            merge_anchor_counts(row["anchor_hit_counts"])
            found_keys = _translation_keys(text, url)
            row["translation_keys"] = found_keys
            merge_translation(found_keys)
        root_assets.append(row)

    key_terms = _key_terms(translation_rows)
    candidates: dict[str, dict[str, Any]] = {}
    all_contexts: list[dict[str, Any]] = []
    request_rows: list[dict[str, Any]] = []

    def merge_candidate(candidate: dict[str, Any]) -> None:
        url = str(candidate["url"])
        current = candidates.get(url)
        if current is None or int(candidate["score"]) > int(current["score"]):
            candidates[url] = candidate

    # Revisit roots with the complete translation key set.
    for url, text in root_sources:
        contexts = _target_contexts(text, url, key_terms)
        all_contexts.extend(contexts)
        for candidate in _dynamic_candidates(text, url, key_terms):
            merge_candidate(candidate)

    dynamic_assets: list[dict[str, Any]] = []
    fetched_dynamic: set[str] = set()
    while len(dynamic_assets) < _MAX_DYNAMIC_ASSETS:
        ranked = sorted(
            (row for row in candidates.values() if str(row["url"]) not in fetched_dynamic),
            key=lambda row: (-int(row["score"]), str(row["url"])),
        )
        if not ranked:
            break
        candidate = ranked[0]
        url = str(candidate["url"])
        fetched_dynamic.add(url)
        result = _fetch(url, _MAX_JS_BYTES)
        text = str(result.get("_text") or "")
        row = _public(result)
        row["candidate"] = candidate
        if text:
            row["anchor_hit_counts"] = _anchor_counts(text)
            merge_anchor_counts(row["anchor_hit_counts"])
            found_keys = _translation_keys(text, url)
            row["translation_keys"] = found_keys
            merge_translation(found_keys)
            # New exact keys can improve subsequent child-chunk ranking.
            key_terms = _key_terms(translation_rows)
            contexts = _target_contexts(text, url, key_terms)
            row["target_contexts"] = contexts
            all_contexts.extend(contexts)
            requests = _request_rows(text, url, key_terms)
            row["mowerbot_requests"] = requests
            request_rows.extend(requests)
            for child in _dynamic_candidates(text, url, key_terms):
                merge_candidate(child)
        dynamic_assets.append(row)

    # Root requests are also useful if the Notification request lives in app-entry.
    for url, text in root_sources:
        request_rows.extend(_request_rows(text, url, key_terms))

    # Keep best evidence per source/path pair.
    unique_requests: dict[tuple[str, str], dict[str, Any]] = {}
    for row in request_rows:
        marker = (str(row.get("source") or ""), str(row.get("path") or ""))
        current = unique_requests.get(marker)
        if current is None or int(row.get("target_score") or 0) > int(current.get("target_score") or 0):
            unique_requests[marker] = row
    ranked_requests = sorted(
        unique_requests.values(),
        key=lambda row: (-int(row.get("target_score") or 0), str(row.get("path") or "")),
    )[:_MAX_REQUEST_ROWS]

    translation_map: dict[str, list[str]] = {}
    for row in translation_rows:
        translation_map.setdefault(row["anchor"], [])
        qualified = row["qualified_key"]
        if qualified not in translation_map[row["anchor"]]:
            translation_map[row["anchor"]].append(qualified)

    bridge_hits: list[dict[str, str]] = []
    for context in all_contexts:
        for bridge in context.get("bridge_calls") or []:
            if bridge not in bridge_hits:
                bridge_hits.append(bridge)
    for request in ranked_requests:
        for bridge in request.get("bridge_calls") or []:
            if bridge not in bridge_hits:
                bridge_hits.append(bridge)

    ranked_candidates = sorted(
        candidates.values(),
        key=lambda row: (-int(row["score"]), str(row["url"])),
    )
    useful_contexts = [
        row
        for row in all_contexts
        if row.get("has_request_hint")
        or row.get("mowerbot_paths")
        or row.get("bridge_calls")
        or row.get("skip_encryption")
    ][:_MAX_REQUEST_CONTEXTS]

    return {
        "read_only": True,
        "public_unauthenticated_only": True,
        "normal_polling_unchanged": True,
        "source": "home_assistant_download",
        "host": host,
        "anchors": list(_ANCHORS),
        "ranking_policy": {
            "strong_anchors": list(_STRONG_ANCHORS),
            "weak_anchors": list(_WEAK_ANCHORS),
            "generic_zero_score_anchors": list(_GENERIC_ANCHORS),
            "seed_keys": list(_SEED_KEYS),
            "note": "All is evidence-only and cannot select a dynamic chunk.",
        },
        "limits": {
            "timeout_seconds_per_request": _TIMEOUT_SECONDS,
            "max_root_assets": _MAX_ROOT_ASSETS,
            "max_dynamic_assets": _MAX_DYNAMIC_ASSETS,
            "max_contexts_per_term": _MAX_CONTEXTS_PER_TERM,
            "max_request_contexts": _MAX_REQUEST_CONTEXTS,
            "context_radius_chars": _CONTEXT_RADIUS,
            "request_radius_chars": _REQUEST_RADIUS,
        },
        "credential_safety": (
            "Public H5 discovery sends no uid, token, mower serial, device id or p:101 payload; source bodies are not stored."
        ),
        "summary": {
            "anchor_hit_counts": anchor_hit_counts,
            "translation_key_map": translation_map,
            "translation_keys": translation_rows,
            "search_key_terms": key_terms,
            "dynamic_candidates": ranked_candidates[:30],
            "mowerbot_requests": ranked_requests,
            "bridge_calls": bridge_hits[:80],
            "request_contexts": useful_contexts,
        },
        "pages": pages,
        "root_assets": root_assets,
        "dynamic_assets": dynamic_assets,
        "note": (
            "Beta28 maps exact Notification translations to keys, ranks lazy chunks by high-signal phrases/keys, and inventories mowerbot request structure in the owning chunks."
        ),
    }
