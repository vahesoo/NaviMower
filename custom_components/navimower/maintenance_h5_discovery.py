"""Read-only public H5 discovery for Navimow Maintenance & Tools."""
from __future__ import annotations

import hashlib
import heapq
import json
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .api.regions import canonical_region
from .diagnostics_sanitize import sanitize

ENTRY_PATHS = (
    "/old/",
    "/maintenance/",
    "/vehicle/maintenance/",
    "/setting/maintenance/",
)
TARGET_TERMS = (
    "componentMaintenance",
    "partsMaintenance",
    "partMaintenance",
    "get-component-maintenance",
    "resetBlade",
    "resetKnife",
    "changeBlade",
    "maintenanceMode",
    "enterMaintenance",
    "exitMaintenance",
    "cutHeight",
    "cuttingHeight",
    "ToolBox",
    "knifeDurationSet",
    "chassisDurationSet",
    "knifeDefaultDuration",
    "chassisDefaultDuration",
    "usedTime",
    "setTime",
)
THEME_TERMS = (
    "maintenance",
    "component",
    "blade",
    "knife",
    "toolbox",
    "service",
    "repair",
    "cutheight",
    "cuttingheight",
    "duration",
)
MAX_HTML = 256 * 1024
MAX_JS = 2 * 1024 * 1024
MAX_ASSETS = 48
MAX_CONTEXTS = 80
MAX_REQUESTS = 120
MAX_JS_CANDIDATES = 160
MAX_UNFETCHED_CANDIDATES = 80
SMALL_JSON_MAX = 8192
CONTEXT_RADIUS = 1800
CANDIDATE_RADIUS = 1400
TIMEOUT = 5

SCRIPT_RE = re.compile(
    r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']",
    re.I,
)
JS_RE = re.compile(
    r"[\"']([^\"'\r\n]{1,420}\.js(?:\?[^\"'\r\n]{0,120})?)[\"']",
    re.I,
)
ENDPOINT_RE = re.compile(
    r"[\"']((?:https?://[^\"'\s]+)?/?(?:mowerbot|vehicle|setting|robot|maintenance|toolbox|api)/[^\"'\r\n]{1,320})[\"']",
    re.I,
)
HTTP_RE = re.compile(
    r"method\s*:\s*[\"']?(GET|POST|PUT|DELETE|PATCH)[\"']?",
    re.I,
)
SKIP_RE = re.compile(r"skipEncryption\s*:\s*(true|false)", re.I)
OBJECT_KEY_RE = re.compile(
    r"(?:^|[,{])\s*[\"']?([A-Za-z_$][\w$]{0,90})[\"']?\s*:"
)
BRIDGE_RE = re.compile(
    r"(?P<callee>(?:[A-Za-z_$][\w$]*\.)*(?:sendEncryptionData|callNative|sendMessageToNative))"
    r"\s*\(\s*[\"'](?P<method>[^\"']{1,160})[\"']",
    re.I,
)


def _host(client: Any) -> str:
    region = canonical_region(getattr(client, "region", "fra"))
    return f"https://navimow-h5-{region}.willand.com"


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", "")
    )


def _fetch(url: str, limit: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": (
                "text/html,application/json,application/javascript,"
                "text/javascript,*/*;q=0.8"
            ),
            "User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.3-beta3",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read(limit + 1)
            status = int(getattr(response, "status", 200))
            content_type = str(response.headers.get("Content-Type", ""))
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
    except Exception as err:  # noqa: BLE001 - optional beta diagnostics
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


def _small_json(result: dict[str, Any]) -> Any | None:
    text = str(result.get("_text") or "")
    content_type = str(result.get("content_type") or "").lower()
    body_length = int(result.get("body_length_read") or 0)
    if not text or "json" not in content_type or body_length > SMALL_JSON_MAX:
        return None
    try:
        return sanitize(json.loads(text))
    except Exception:  # noqa: BLE001 - diagnostics preview only
        return sanitize(text[:SMALL_JSON_MAX])


def _endpoint_path(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme and parsed.netloc:
        return parsed.path
    return value.split("?", 1)[0].split("#", 1)[0]


def _object_keys(text: str) -> list[str]:
    ignored = {"class", "style", "children", "props", "key", "ref", "render"}
    rows: list[str] = []
    for key in OBJECT_KEY_RE.findall(text):
        if key.lower() in ignored or key in rows:
            continue
        rows.append(key)
        if len(rows) >= 100:
            break
    return rows


def _bridge_calls(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for match in BRIDGE_RE.finditer(text):
        row = {
            "callee": match.group("callee"),
            "method": match.group("method"),
        }
        if row not in rows:
            rows.append(row)
        if len(rows) >= 48:
            break
    return rows


def _matched_terms(text: str) -> list[str]:
    lower = text.lower()
    return [term for term in TARGET_TERMS if term.lower() in lower]


def _structure(text: str) -> dict[str, Any]:
    endpoint_paths = sorted(
        {
            _endpoint_path(value)
            for value in ENDPOINT_RE.findall(text)
            if value
        }
    )[:96]
    return {
        "matched_terms": _matched_terms(text),
        "endpoint_paths": endpoint_paths,
        "http_methods": sorted(
            {value.upper() for value in HTTP_RE.findall(text)}
        ),
        "skip_encryption": sorted(
            {value.lower() for value in SKIP_RE.findall(text)}
        ),
        "object_keys": _object_keys(text),
        "bridge_calls": _bridge_calls(text),
    }


def _contexts(text: str, source: str) -> list[dict[str, Any]]:
    lower = text.lower()
    rows: list[dict[str, Any]] = []
    for term in TARGET_TERMS:
        start = 0
        found = 0
        needle = term.lower()
        while found < 3 and len(rows) < MAX_CONTEXTS:
            index = lower.find(needle, start)
            if index < 0:
                break
            lo = max(0, index - CONTEXT_RADIUS)
            hi = min(len(text), index + len(term) + CONTEXT_RADIUS)
            nearby = text[lo:hi]
            rows.append(
                {
                    "term": term,
                    "source": _safe_url(source),
                    **_structure(nearby),
                    "context": re.sub(r"\s+", " ", nearby).strip(),
                }
            )
            start = index + len(needle)
            found += 1
    return rows


def _candidate_score(text: str, match: re.Match[str], url: str) -> tuple[int, list[str]]:
    lo = max(0, match.start() - CANDIDATE_RADIUS)
    hi = min(len(text), match.end() + CANDIDATE_RADIUS)
    nearby = text[lo:hi].lower()
    url_lower = url.lower()
    terms = sorted(
        {
            term
            for term in THEME_TERMS
            if term in nearby or term in url_lower
        }
    )
    score = len(terms) * 8
    if any(term.lower() in nearby for term in TARGET_TERMS):
        score += 80
    if any(
        token in url_lower
        for token in (
            "maintenance",
            "toolbox",
            "knife",
            "blade",
            "component",
            "service",
            "repair",
        )
    ):
        score += 40
    return score, terms


def _js_candidates(
    text: str,
    base_url: str,
    allowed_hosts: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for order, match in enumerate(JS_RE.finditer(text)):
        url = _safe_url(
            urllib.parse.urljoin(base_url, match.group(1).strip())
        )
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.netloc not in allowed_hosts
            or url in seen
        ):
            continue
        seen.add(url)
        score, terms = _candidate_score(text, match, url)
        rows.append(
            {
                "url": url,
                "source": _safe_url(base_url),
                "score": score,
                "theme_terms": terms,
                "order": order,
            }
        )
    rows.sort(
        key=lambda row: (
            -int(row["score"]),
            int(row["order"]),
            str(row["url"]),
        )
    )
    return rows


def probe_maintenance_h5(client: Any) -> dict[str, Any]:
    """Inspect public H5 source for Maintenance & Tools request structure."""
    host = _host(client)
    host_name = urllib.parse.urlsplit(host).netloc
    pages: list[dict[str, Any]] = []
    root_scripts: list[str] = []
    allowed_hosts: set[str] = {host_name}

    for entry_path in ENTRY_PATHS:
        url = urllib.parse.urljoin(host + "/", entry_path.lstrip("/"))
        result = _fetch(url, MAX_HTML)
        text = str(result.get("_text") or "")
        row = _public(result)
        json_body = _small_json(result)
        if json_body is not None:
            row["json_body"] = json_body
        scripts: list[str] = []
        if text:
            for value in SCRIPT_RE.findall(text):
                asset = _safe_url(
                    urllib.parse.urljoin(url, value.strip())
                )
                parsed = urllib.parse.urlsplit(asset)
                if parsed.scheme != "https" or not parsed.netloc:
                    continue
                allowed_hosts.add(parsed.netloc)
                if asset not in scripts:
                    scripts.append(asset)
            row["script_urls"] = scripts[:12]
            root_scripts.extend(scripts[:12])
        pages.append(row)

    queue: list[tuple[int, int, str, str]] = []
    queued: set[str] = set()
    sequence = 0
    for url in root_scripts:
        if url in queued:
            continue
        queued.add(url)
        heapq.heappush(queue, (-1000, sequence, url, "root_script"))
        sequence += 1

    assets: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []
    request_candidates: list[dict[str, Any]] = []
    bridge_candidates: list[dict[str, Any]] = []
    js_candidates: dict[str, dict[str, Any]] = {}
    fetched: set[str] = set()
    request_markers: set[tuple[str, str]] = set()
    bridge_markers: set[tuple[str, str, str]] = set()

    while queue and len(assets) < MAX_ASSETS:
        neg_score, _, url, reason = heapq.heappop(queue)
        if url in fetched:
            continue
        fetched.add(url)
        result = _fetch(url, MAX_JS)
        text = str(result.get("_text") or "")
        row = _public(result)
        row["kind"] = "asset"
        row["discovery_reason"] = reason
        row["candidate_score"] = -neg_score if neg_score != -1000 else None

        if text:
            structure = _structure(text)
            row.update(structure)
            found_contexts = _contexts(text, url)
            contexts.extend(found_contexts)

            for path in structure["endpoint_paths"]:
                marker = (str(path), _safe_url(url))
                if marker in request_markers:
                    continue
                request_markers.add(marker)
                request_candidates.append(
                    {
                        "path": path,
                        "source": _safe_url(url),
                        "matched_terms": structure["matched_terms"],
                        "http_methods": structure["http_methods"],
                        "skip_encryption": structure["skip_encryption"],
                        "object_keys": structure["object_keys"],
                        "bridge_calls": structure["bridge_calls"],
                    }
                )

            for bridge in structure["bridge_calls"]:
                marker = (
                    str(bridge.get("callee")),
                    str(bridge.get("method")),
                    _safe_url(url),
                )
                if marker in bridge_markers:
                    continue
                bridge_markers.add(marker)
                bridge_candidates.append(
                    {
                        **bridge,
                        "source": _safe_url(url),
                        "matched_terms": structure["matched_terms"],
                    }
                )

            candidates = _js_candidates(text, url, allowed_hosts)
            row["js_reference_count"] = len(candidates)
            for candidate in candidates:
                candidate_url = str(candidate["url"])
                previous = js_candidates.get(candidate_url)
                if previous is None or int(candidate["score"]) > int(previous["score"]):
                    js_candidates[candidate_url] = candidate
                if candidate_url in queued or candidate_url in fetched:
                    continue
                queued.add(candidate_url)
                score = int(candidate["score"])
                reason_text = (
                    "scored_lazy_chunk" if score > 0 else "bounded_lazy_chunk"
                )
                heapq.heappush(
                    queue,
                    (-score, sequence, candidate_url, reason_text),
                )
                sequence += 1
        assets.append(row)

    unique_contexts: list[dict[str, Any]] = []
    context_markers: set[tuple[str, str, str]] = set()
    for row in contexts:
        marker = (
            str(row["term"]),
            str(row["source"]),
            str(row["context"]),
        )
        if marker in context_markers:
            continue
        context_markers.add(marker)
        unique_contexts.append(row)
        if len(unique_contexts) >= MAX_CONTEXTS:
            break

    request_candidates.sort(
        key=lambda row: (
            0 if row["matched_terms"] else 1,
            str(row["path"]),
            str(row["source"]),
        )
    )
    request_candidates = request_candidates[:MAX_REQUESTS]

    bridge_candidates.sort(
        key=lambda row: (
            0 if row["matched_terms"] else 1,
            str(row.get("method")),
        )
    )
    bridge_candidates = bridge_candidates[:MAX_REQUESTS]

    candidate_rows = sorted(
        js_candidates.values(),
        key=lambda row: (
            -int(row["score"]),
            int(row["order"]),
            str(row["url"]),
        ),
    )[:MAX_JS_CANDIDATES]
    unfetched = [
        row for row in candidate_rows if str(row["url"]) not in fetched
    ][:MAX_UNFETCHED_CANDIDATES]

    return {
        "read_only": True,
        "beta_only": True,
        "public_unauthenticated_h5_only": True,
        "normal_mower_polling_unchanged": True,
        "mutation_calls_executed": False,
        "source": "home_assistant_download",
        "host": host,
        "entry_paths": list(ENTRY_PATHS),
        "targets": list(TARGET_TERMS),
        "theme_terms": list(THEME_TERMS),
        "limits": {
            "timeout_seconds_per_request": TIMEOUT,
            "max_html_bytes": MAX_HTML,
            "max_js_bytes_per_asset": MAX_JS,
            "max_assets": MAX_ASSETS,
            "max_contexts": MAX_CONTEXTS,
            "max_request_candidates": MAX_REQUESTS,
            "max_js_candidates": MAX_JS_CANDIDATES,
            "small_json_max_bytes": SMALL_JSON_MAX,
        },
        "credential_safety": (
            "No token, cookie, uid, device id, mower serial or encrypted "
            "p:101 business payload is sent to H5. Only public GET resources "
            "are read."
        ),
        "investigation_goal": (
            "Recover official Maintenance & Tools request structure for blade "
            "runtime reset and mower maintenance mode, including lazy chunks, "
            "small JSON route responses, endpoint paths, payload keys, HTTP "
            "methods, encryption flags and native bridge calls."
        ),
        "pages": pages,
        "assets": assets,
        "contexts": unique_contexts,
        "request_candidates": request_candidates,
        "bridge_candidates": bridge_candidates,
        "js_discovery": {
            "allowed_hosts": sorted(allowed_hosts),
            "root_script_count": len(set(root_scripts)),
            "candidate_count": len(js_candidates),
            "fetched_asset_count": len(fetched),
            "candidates": candidate_rows,
            "unfetched_candidates": unfetched,
        },
        "note": (
            "0.4.3-beta3 broadens bounded public H5 discovery only. It does "
            "not reset maintenance counters, enter maintenance mode, change "
            "cutting height or execute any mower command."
        ),
    }
