"""Read-only H5 discovery for the main Navimow Notification feed.

Beta28 first maps exact visible Notification translations to their JavaScript
translation keys, then uses only high-signal phrases/keys to rank lazy chunks.
Generic words such as All/System/Device remain visible as evidence but cannot
dominate chunk selection. Selected chunks are inspected for literal mowerbot
requests, HTTP method hints, skipEncryption, payload/object keys and native or
encryption bridge calls.

No credentials, mower serials, account ids, device ids or p:101 payloads are
sent to H5. Full HTML/JavaScript bodies are never persisted in diagnostics.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .diagnostics_export import sanitize

_ANCHORS = (
    "All",
    "Important",
    "Work status",
    "System",
    "Device",
    "newMessages",
    "No more messages",
    "Failed to load new messages",
)
_STRONG_ANCHORS = (
    "Important",
    "Work status",
    "newMessages",
    "No more messages",
    "Failed to load new messages",
)
_WEAK_ANCHORS = ("System", "Device")
_GENERIC_ANCHORS = ("All",)
_SEED_KEYS = ("newMessages", "messageCenter")
_ENTRY_PATHS = ("/message/message/list", "/old/")
_MAX_HTML_BYTES = 256 * 1024
_MAX_JS_BYTES = 2 * 1024 * 1024
_MAX_ROOT_ASSETS = 6
_MAX_DYNAMIC_ASSETS = 6
_MAX_CONTEXTS_PER_TERM = 4
_MAX_REQUEST_CONTEXTS = 48
_CONTEXT_RADIUS = 2200
_REQUEST_RADIUS = 3200
_DYNAMIC_RADIUS = 2600
_TIMEOUT_SECONDS = 5

_SCRIPT_RE = re.compile(r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
_JS_RE = re.compile(r"[\"']([^\"'\r\n]{1,360}\.js(?:\?[^\"'\r\n]{0,120})?)[\"']", re.I)
_MOWERBOT_RE = re.compile(r"[\"'](/mowerbot/[^\"'\r\n]{1,240})[\"']", re.I)
_HTTP_RE = re.compile(r"method\s*:\s*[\"']?(GET|POST|PUT|DELETE|PATCH)[\"']?", re.I)
_SKIP_RE = re.compile(r"skipEncryption\s*:\s*(true|false)", re.I)
_BRIDGE_RE = re.compile(
    r"(?P<callee>(?:[A-Za-z_$][\w$]*\.)*(?:sendEncryptionData|callNative|sendMessageToNative))"
    r"\s*\(\s*[\"'](?P<method>[^\"']{1,140})[\"']",
    re.I,
)
_OBJECT_KEY_RE = re.compile(r"(?:^|[,{])\s*[\"']?([A-Za-z_$][\w$]{1,80})[\"']?\s*:")
_REQUEST_HINT_RE = re.compile(
    r"(?:/mowerbot/|skipEncryption|sendEncryptionData|callNative|sendMessageToNative|"
    r"axios|fetch|\.post\(|\.get\(|\.request\(|method\s*:)",
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
    found: list[str] = []
    for src in _SCRIPT_RE.findall(html):
        url = urllib.parse.urljoin(base_url, src.strip())
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "https" and parsed.netloc and url not in found:
            found.append(url)
    found.sort(
        key=lambda value: (
            0 if any(word in value.lower() for word in ("app", "main", "entry", "index")) else 1,
            len(value),
        )
    )
    return found[:_MAX_ROOT_ASSETS]


def _anchor_counts(text: str) -> dict[str, int]:
    lower = text.lower()
    return {anchor: lower.count(anchor.lower()) for anchor in _ANCHORS if anchor.lower() in lower}


def _translation_rows(text: str, source: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for anchor in _ANCHORS:
        escaped = re.escape(anchor)
        patterns = (
            re.compile(rf"(?P<key>[A-Za-z_$][\w$.-]{{0,100}})\s*:\s*[\"']{escaped}[\"']", re.I),
            re.compile(rf"[\"'](?P<key>[^\"'\r\n]{{1,120}})[\"']\s*:\s*[\"']{escaped}[\"']", re.I),
        )
        for pattern in patterns:
            for match in pattern.finditer(text):
                key = str(match.group("key")).strip()
                marker = (anchor.lower(), key.lower())
                if not key or marker in seen:
                    continue
                seen.add(marker)
                lo = max(0, match.start() - 280)
                hi = min(len(text), match.end() + 280)
                rows.append(
                    {
                        "anchor": anchor,
                        "key": key,
                        "source": _safe_url(source),
                        "context": re.sub(r"\s+", " ", text[lo:hi]).strip(),
                    }
                )
                if len(rows) >= 80:
                    return rows
    return rows


def _ranking_keys(rows: list[dict[str, str]]) -> list[str]:
    """Only strong anchors may contribute discovered keys to chunk scoring."""
    values = set(_SEED_KEYS)
    for row in rows:
        if row.get("anchor") not in _STRONG_ANCHORS:
            continue
        key = str(row.get("key") or "").strip()
        if len(key) >= 3:
            values.add(key)
    return sorted(values, key=lambda value: (-len(value), value.lower()))


def _bridge_calls(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for match in _BRIDGE_RE.finditer(text):
        row = {"callee": match.group("callee"), "method": match.group("method")}
        if row not in rows:
            rows.append(row)
        if len(rows) >= 20:
            break
    return rows


def _object_keys(text: str) -> list[str]:
    ignored = {"class", "style", "children", "props", "key", "ref", "type", "name", "render"}
    values: list[str] = []
    for key in _OBJECT_KEY_RE.findall(text):
        if key.lower() in ignored or key in values:
            continue
        values.append(key)
        if len(values) >= 40:
            break
    return values


def _matched(text: str, keys: list[str]) -> tuple[list[str], list[str], list[str], int]:
    lower = text.lower()
    strong = [value for value in _STRONG_ANCHORS if value.lower() in lower]
    weak = [value for value in _WEAK_ANCHORS if value.lower() in lower]
    key_hits = [value for value in keys if value.lower() in lower]
    themes = [
        value
        for value in ("messagecenter", "notification", "newmessages", "messagehistory")
        if value in lower
    ]
    score = len(strong) * 8 + len(key_hits) * 10 + len(themes) * 3 + len(weak)
    return strong + weak, key_hits, themes, score


def _dynamic_candidates(text: str, base_url: str, keys: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _JS_RE.finditer(text):
        url = _safe_url(urllib.parse.urljoin(base_url, match.group(1).strip()))
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or url in seen:
            continue
        lo = max(0, match.start() - _DYNAMIC_RADIUS)
        hi = min(len(text), match.end() + _DYNAMIC_RADIUS)
        anchors, key_hits, themes, score = _matched(text[lo:hi], keys)
        # All has no score and cannot select a chunk; weak anchors alone are insufficient.
        if score < 3:
            continue
        seen.add(url)
        rows.append(
            {
                "url": url,
                "anchors": anchors,
                "translation_keys": key_hits[:20],
                "theme_terms": themes,
                "score": score,
            }
        )
    return sorted(rows, key=lambda row: (-int(row["score"]), str(row["url"])))


def _context(text: str, idx: int, term: str, kind: str, source: str) -> dict[str, Any]:
    lo = max(0, idx - _CONTEXT_RADIUS)
    hi = min(len(text), idx + len(term) + _CONTEXT_RADIUS)
    raw = text[lo:hi]
    return {
        "term": term,
        "kind": kind,
        "source": _safe_url(source),
        "mowerbot_paths": sorted(set(_MOWERBOT_RE.findall(raw)))[:20],
        "http_methods": sorted({value.upper() for value in _HTTP_RE.findall(raw)}),
        "skip_encryption": sorted({value.lower() for value in _SKIP_RE.findall(raw)}),
        "bridge_calls": _bridge_calls(raw),
        "object_keys": _object_keys(raw),
        "has_request_hint": bool(_REQUEST_HINT_RE.search(raw)),
        "context": re.sub(r"\s+", " ", raw).strip(),
    }


def _target_contexts(text: str, source: str, keys: list[str]) -> list[dict[str, Any]]:
    """Use a separate quota per anchor/key, so one common term cannot fill the list."""
    lower = text.lower()
    rows: list[dict[str, Any]] = []
    terms: list[tuple[str, str, int]] = []
    terms += [(value, "anchor", _MAX_CONTEXTS_PER_TERM) for value in _STRONG_ANCHORS]
    terms += [(value, "anchor", 2) for value in _WEAK_ANCHORS]
    terms += [(value, "translation_key", _MAX_CONTEXTS_PER_TERM) for value in keys]
    # All is deliberately not a request-context term.
    for term, kind, limit in terms:
        start = 0
        kept = 0
        needle = term.lower()
        while kept < limit and len(rows) < _MAX_REQUEST_CONTEXTS:
            idx = lower.find(needle, start)
            if idx < 0:
                break
            start = idx + max(1, len(needle))
            rows.append(_context(text, idx, term, kind, source))
            kept += 1
    return rows


def _request_rows(text: str, source: str, keys: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _MOWERBOT_RE.finditer(text):
        path = match.group(1)
        if path in seen:
            continue
        seen.add(path)
        lo = max(0, match.start() - _REQUEST_RADIUS)
        hi = min(len(text), match.end() + _REQUEST_RADIUS)
        raw = text[lo:hi]
        anchors, key_hits, themes, score = _matched(raw, keys)
        rows.append(
            {
                "path": path,
                "source": _safe_url(source),
                "target_score": score,
                "matched_anchors": anchors,
                "matched_translation_keys": key_hits[:20],
                "theme_terms": themes,
                "http_methods": sorted({value.upper() for value in _HTTP_RE.findall(raw)}),
                "skip_encryption": sorted({value.lower() for value in _SKIP_RE.findall(raw)}),
                "bridge_calls": _bridge_calls(raw),
                "object_keys": _object_keys(raw),
                "context": re.sub(r"\s+", " ", raw).strip(),
            }
        )
        if len(rows) >= 60:
            break
    return sorted(rows, key=lambda row: (-int(row["target_score"]), str(row["path"])))


def probe_main_notification_feed(client: Any) -> dict[str, Any]:
    """Discover request clues for Notification -> Device from public H5 source."""
    host = _host(client)
    pages: list[dict[str, Any]] = []
    scripts: list[str] = []
    page_hashes: set[str] = set()
    translations: list[dict[str, str]] = []
    anchor_counts: dict[str, int] = {}

    def add_counts(values: dict[str, int]) -> None:
        for key, value in values.items():
            anchor_counts[key] = anchor_counts.get(key, 0) + int(value)

    def add_translations(values: list[dict[str, str]]) -> None:
        known = {(row["anchor"].lower(), row["key"].lower()) for row in translations}
        for row in values:
            marker = (row["anchor"].lower(), row["key"].lower())
            if marker not in known and len(translations) < 80:
                translations.append(row)
                known.add(marker)

    for path in _ENTRY_PATHS:
        url = urllib.parse.urljoin(host + "/", path.lstrip("/"))
        result = _fetch(url, _MAX_HTML_BYTES)
        text = str(result.get("_text") or "")
        row = _public(result)
        if text:
            found = _root_scripts(text, url)
            row["script_urls"] = [_safe_url(value) for value in found]
            row["anchor_hit_counts"] = _anchor_counts(text)
            add_counts(row["anchor_hit_counts"])
            found_translations = _translation_rows(text, url)
            row["translation_keys"] = found_translations
            add_translations(found_translations)
            digest = str(result.get("body_sha256") or "")
            if digest not in page_hashes:
                scripts += found
                page_hashes.add(digest)
        pages.append(row)

    root_sources: list[tuple[str, str]] = []
    root_assets: list[dict[str, Any]] = []
    for url in list(dict.fromkeys(scripts))[:_MAX_ROOT_ASSETS]:
        result = _fetch(url, _MAX_JS_BYTES)
        text = str(result.get("_text") or "")
        row = _public(result)
        if text:
            root_sources.append((url, text))
            row["anchor_hit_counts"] = _anchor_counts(text)
            add_counts(row["anchor_hit_counts"])
            found_translations = _translation_rows(text, url)
            row["translation_keys"] = found_translations
            add_translations(found_translations)
        root_assets.append(row)

    ranking_keys = _ranking_keys(translations)
    candidates: dict[str, dict[str, Any]] = {}
    contexts: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []

    def add_candidates(text: str, url: str) -> None:
        for candidate in _dynamic_candidates(text, url, ranking_keys):
            old = candidates.get(candidate["url"])
            if old is None or int(candidate["score"]) > int(old["score"]):
                candidates[candidate["url"]] = candidate

    for url, text in root_sources:
        contexts += _target_contexts(text, url, ranking_keys)
        requests += _request_rows(text, url, ranking_keys)
        add_candidates(text, url)

    dynamic_assets: list[dict[str, Any]] = []
    fetched: set[str] = set()
    while len(dynamic_assets) < _MAX_DYNAMIC_ASSETS:
        ranked = sorted(
            (row for row in candidates.values() if row["url"] not in fetched),
            key=lambda row: (-int(row["score"]), str(row["url"])),
        )
        if not ranked:
            break
        candidate = ranked[0]
        url = str(candidate["url"])
        fetched.add(url)
        result = _fetch(url, _MAX_JS_BYTES)
        text = str(result.get("_text") or "")
        row = _public(result)
        row["candidate"] = candidate
        if text:
            row["anchor_hit_counts"] = _anchor_counts(text)
            add_counts(row["anchor_hit_counts"])
            found_translations = _translation_rows(text, url)
            row["translation_keys"] = found_translations
            add_translations(found_translations)
            ranking_keys = _ranking_keys(translations)
            row["target_contexts"] = _target_contexts(text, url, ranking_keys)
            contexts += row["target_contexts"]
            row["mowerbot_requests"] = _request_rows(text, url, ranking_keys)
            requests += row["mowerbot_requests"]
            add_candidates(text, url)
        dynamic_assets.append(row)

    translation_map: dict[str, list[str]] = {}
    for row in translations:
        translation_map.setdefault(row["anchor"], [])
        if row["key"] not in translation_map[row["anchor"]]:
            translation_map[row["anchor"]].append(row["key"])

    request_best: dict[tuple[str, str], dict[str, Any]] = {}
    for row in requests:
        marker = (str(row["source"]), str(row["path"]))
        old = request_best.get(marker)
        if old is None or int(row["target_score"]) > int(old["target_score"]):
            request_best[marker] = row
    ranked_requests = sorted(
        request_best.values(),
        key=lambda row: (-int(row["target_score"]), str(row["path"])),
    )[:60]

    bridge_calls: list[dict[str, str]] = []
    for row in [*contexts, *ranked_requests]:
        for bridge in row.get("bridge_calls") or []:
            if bridge not in bridge_calls:
                bridge_calls.append(bridge)

    useful_contexts = [
        row
        for row in contexts
        if row.get("has_request_hint")
        or row.get("mowerbot_paths")
        or row.get("bridge_calls")
        or row.get("skip_encryption")
    ][:_MAX_REQUEST_CONTEXTS]
    ranked_candidates = sorted(
        candidates.values(),
        key=lambda row: (-int(row["score"]), str(row["url"])),
    )

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
            "note": "All is evidence-only; discovered keys from All/System/Device are excluded from chunk scoring.",
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
            "anchor_hit_counts": anchor_counts,
            "translation_key_map": translation_map,
            "translation_keys": translations,
            "ranking_translation_keys": ranking_keys,
            "dynamic_candidates": ranked_candidates[:30],
            "mowerbot_requests": ranked_requests,
            "bridge_calls": bridge_calls[:80],
            "request_contexts": useful_contexts,
        },
        "pages": pages,
        "root_assets": root_assets,
        "dynamic_assets": dynamic_assets,
        "note": (
            "Beta28 discovers translation keys first, excludes generic labels from ranking, and inventories request structure in high-signal Notification chunks."
        ),
    }
