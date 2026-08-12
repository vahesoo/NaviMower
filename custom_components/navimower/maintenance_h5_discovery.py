"""Read-only public H5 contract discovery for Maintenance and Mowing Reports."""
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
    "/setting/maintenance/",
)

MAINTENANCE_TARGETS = (
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
    "knifeDurationSet",
    "chassisDurationSet",
    "knifeDefaultDuration",
    "chassisDefaultDuration",
    "usedTime",
    "setTime",
)

MAINTENANCE_UI_TARGETS = (
    "Parts maintenance",
    "Blades",
    "Used time",
    "Remaining time",
    "Replacement done",
    "Chassis and other parts",
    "Clean now",
    "Buy now",
    "Check now",
    "replacementDone",
    "replaceDone",
    "cleanNow",
    "remainingTime",
    "tutorialVideosUrl",
    "mallEntranceUrl",
    "knife",
    "chassis",
)

REPORT_ENDPOINTS = (
    "/vehicle/report/get-day-week-month-data",
    "/vehicle/report/vehicle-main-report",
)

REPORT_TARGETS = (
    "mowingReport",
    "mowing_report",
    "mowingArea",
    "mowing_area",
    "mowingTime",
    "mowing_time",
    "mowingCount",
    "mowing_count",
    "totalMowingArea",
    "totalMowingTime",
)

REPORT_TRANSPORT_TARGETS = (
    "handleEncipherment",
    "handleDecrypt",
    "sendEncryptionData",
    "keyDataOne",
    "keyDataTwo",
    "keyDataThree",
    "keyDataFour",
    "timeStamp",
    "body:{data",
    '"p":"101"',
)

TARGET_TERMS = (
    MAINTENANCE_TARGETS
    + MAINTENANCE_UI_TARGETS
    + REPORT_ENDPOINTS
    + REPORT_TARGETS
)
REQUEST_SHAPE_TERMS = (
    "handleH5MowerSet",
    "skipEncryption",
    "needRawResponse",
    "handleEncipherment",
    "handleDecrypt",
)
THEME_TERMS = (
    "maintenance",
    "parts",
    "blade",
    "knife",
    "chassis",
    "replacement",
    "clean",
    "repair",
    "report",
    "mowing",
    "duration",
)
PRIORITY_FILENAME_TOKENS = (
    "maintenance",
    "repair",
    "parts",
    "blade",
    "knife",
    "chassis",
    "report",
    "request-",
    "native-",
    "service-",
    "component",
    "mower",
    "setting",
)
TARGETED_THEME_TERMS = (
    "maintenance",
    "repair",
    "parts",
    "blade",
    "knife",
    "chassis",
    "replacement",
    "clean",
)

MAX_HTML = 256 * 1024
MAX_JS = 2 * 1024 * 1024
MAX_ASSETS = 48
MAX_TARGETED_ASSETS = 24
MAX_REQUESTS = 160
MAX_CONTEXTS = 112
MAX_REQUEST_CANDIDATES = 180
MAX_JS_CANDIDATES = 220
MAX_UNFETCHED_CANDIDATES = 120
SMALL_JSON_MAX = 8192
MAX_SOURCE_MAPS = 6
MAX_SOURCE_MAP = 4 * 1024 * 1024
MAX_SOURCE_MAP_MATCHING_SOURCES = 32
CONTEXT_RADIUS = 2200
CANDIDATE_RADIUS = 1500
CALLSITE_RADIUS = 2600
MAX_CALLSITES_PER_WRAPPER = 8
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
SOURCE_MAP_RE = re.compile(r"sourceMappingURL\s*=\s*([^\s*]+)", re.I)
QUOTED_STRING_RE = re.compile(r"[\"']([^\"'\r\n]{2,180})[\"']")
REPORT_WRAPPER_RE = re.compile(
    r"function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\(\s*(?P<param>[A-Za-z_$][\w$]*)\s*\)"
    r"\s*\{.{0,1400}?[\"'](?P<endpoint>/vehicle/report/(?:get-day-week-month-data|vehicle-main-report))[\"']",
    re.I | re.S,
)
REPORT_ARROW_WRAPPER_RE = re.compile(
    r"(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(?\s*(?P<param>[A-Za-z_$][\w$]*)\s*\)?"
    r"\s*=>.{0,1400}?[\"'](?P<endpoint>/vehicle/report/(?:get-day-week-month-data|vehicle-main-report))[\"']",
    re.I | re.S,
)
MOWER_SET_WRAPPER_RE = re.compile(
    r"function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\(\s*(?P<param>[A-Za-z_$][\w$]*)"
    r"(?:\s*=\s*\{\})?\s*\)\s*\{[^{}]{0,700}?"
    r"(?:callNative|sendMessageToNative)\s*\(\s*[\"']handleH5MowerSet[\"']",
    re.I | re.S,
)
MOWER_SET_ARROW_WRAPPER_RE = re.compile(
    r"(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(?\s*(?P<param>[A-Za-z_$][\w$]*)"
    r"(?:\s*=\s*\{\})?\s*\)?\s*=>[^;]{0,700}?"
    r"(?:callNative|sendMessageToNative)\s*\(\s*[\"']handleH5MowerSet[\"']",
    re.I | re.S,
)


def _host(client: Any) -> str:
    region = canonical_region(getattr(client, "region", "fra"))
    return f"https://navimow-h5-{region}.willand.com"


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url))
    path = parsed.path
    while "/static/js/static/js/" in path:
        path = path.replace("/static/js/static/js/", "/static/js/")
    while "/assets/assets/" in path:
        path = path.replace("/assets/assets/", "/assets/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _resolve_js_url(base_url: str, value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed_ref = urllib.parse.urlsplit(raw)
    if parsed_ref.scheme and parsed_ref.netloc:
        return _safe_url(raw)

    base = urllib.parse.urlsplit(base_url)
    clean = raw.lstrip("./")
    for marker in ("static/js/", "assets/"):
        if not clean.startswith(marker):
            continue
        marker_index = base.path.find("/" + marker)
        if marker_index < 0:
            continue
        prefix = base.path[: marker_index + 1]
        candidate = urllib.parse.urlunsplit(
            (base.scheme, base.netloc, prefix + clean, "", "")
        )
        return _safe_url(candidate)
    return _safe_url(urllib.parse.urljoin(base_url, raw))


def _fetch(url: str, limit: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": (
                "text/html,application/json,application/javascript,"
                "text/javascript,*/*;q=0.8"
            ),
            "User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.3-beta6",
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
        if len(rows) >= 120:
            break
    return rows


def _bridge_calls(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for match in BRIDGE_RE.finditer(text):
        row = {"callee": match.group("callee"), "method": match.group("method")}
        if row not in rows:
            rows.append(row)
        if len(rows) >= 64:
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
    )[:128]
    request_markers = [
        term for term in REQUEST_SHAPE_TERMS if term.lower() in text.lower()
    ]
    return {
        "matched_terms": _matched_terms(text),
        "endpoint_paths": endpoint_paths,
        "http_methods": sorted({value.upper() for value in HTTP_RE.findall(text)}),
        "skip_encryption": sorted({value.lower() for value in SKIP_RE.findall(text)}),
        "request_shape_markers": request_markers,
        "object_keys": _object_keys(text),
        "bridge_calls": _bridge_calls(text),
    }


def _context_around(text: str, needle: str, radius: int = CONTEXT_RADIUS) -> str:
    index = text.lower().find(needle.lower())
    if index < 0:
        return ""
    lo = max(0, index - radius)
    hi = min(len(text), index + len(needle) + radius)
    return re.sub(r"\s+", " ", text[lo:hi]).strip()


def _contexts_for_terms(
    text: str,
    source: str,
    terms: tuple[str, ...],
    focus: str,
    *,
    max_per_term: int = 2,
) -> list[dict[str, Any]]:
    lower = text.lower()
    rows: list[dict[str, Any]] = []
    for term in terms:
        start = 0
        found = 0
        needle = term.lower()
        while found < max_per_term and len(rows) < MAX_CONTEXTS:
            index = lower.find(needle, start)
            if index < 0:
                break
            lo = max(0, index - CONTEXT_RADIUS)
            hi = min(len(text), index + len(term) + CONTEXT_RADIUS)
            nearby = text[lo:hi]
            rows.append(
                {
                    "focus": focus,
                    "term": term,
                    "source": _safe_url(source),
                    **_structure(nearby),
                    "context": re.sub(r"\s+", " ", nearby).strip(),
                }
            )
            start = index + len(needle)
            found += 1
    return rows



def _balanced_argument(text: str, open_index: int, limit: int = 1800) -> tuple[str, bool]:
    """Return the first balanced call argument body after an opening parenthesis."""
    depth = 0
    quote: str | None = None
    escaped = False
    end_limit = min(len(text), open_index + limit)
    for index in range(open_index, end_limit):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")":
            depth -= 1
            if depth == 0:
                value = text[open_index + 1:index]
                return re.sub(r"\s+", " ", value).strip(), False
    value = text[open_index + 1:end_limit]
    return re.sub(r"\s+", " ", value).strip(), True


def _wrapper_definitions(
    text: str,
    source: str,
    regexes: tuple[tuple[str, re.Pattern[str]], ...],
    focus: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for wrapper_kind, regex in regexes:
        for match in regex.finditer(text):
            name = match.group("name")
            endpoint = match.groupdict().get("endpoint") or ""
            marker = (name, endpoint, match.start())
            if marker in seen:
                continue
            seen.add(marker)
            nearby = text[
                max(0, match.start() - 350):
                min(len(text), match.end() + 1200)
            ]
            rows.append(
                {
                    "focus": focus,
                    "wrapper_kind": wrapper_kind,
                    "name": name,
                    "param": match.groupdict().get("param") or "",
                    "endpoint": endpoint or None,
                    "source": _safe_url(source),
                    "definition_offset": match.start(),
                    **_structure(nearby),
                    "context": re.sub(r"\s+", " ", nearby).strip(),
                }
            )
            if len(rows) >= 32:
                return rows
    return rows


def _named_callsite_contexts(
    text: str,
    source: str,
    definitions: list[dict[str, Any]],
    focus: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for definition in definitions:
        name = str(definition.get("name") or "")
        if not name:
            continue
        definition_offset = int(definition.get("definition_offset") or -10_000)
        captured = 0
        for match in re.finditer(r"\b" + re.escape(name) + r"\s*\(", text):
            if definition_offset - 20 <= match.start() <= definition_offset + 180:
                continue
            open_index = text.find("(", match.start(), match.end() + 1)
            if open_index < 0:
                continue
            argument_preview, argument_truncated = _balanced_argument(text, open_index)
            lo = max(0, match.start() - CALLSITE_RADIUS)
            hi = min(len(text), match.end() + CALLSITE_RADIUS)
            nearby = text[lo:hi]
            lower = nearby.lower()
            report_terms = [term for term in REPORT_TARGETS if term.lower() in lower]
            maintenance_terms = [
                term
                for term in (
                    MAINTENANCE_TARGETS
                    + MAINTENANCE_UI_TARGETS
                    + ("blade", "knife", "chassis", "repair")
                )
                if term.lower() in lower
            ]
            rows.append(
                {
                    "focus": focus,
                    "wrapper_name": name,
                    "endpoint": definition.get("endpoint"),
                    "source": _safe_url(source),
                    "call_offset": match.start(),
                    "argument_preview": argument_preview[:1800],
                    "argument_truncated": argument_truncated,
                    "report_terms_nearby": report_terms,
                    "maintenance_terms_nearby": maintenance_terms,
                    **_structure(nearby),
                    "context": re.sub(r"\s+", " ", nearby).strip(),
                }
            )
            captured += 1
            if captured >= MAX_CALLSITES_PER_WRAPPER or len(rows) >= 64:
                break
    return rows


def _callsite_findings(text: str, source: str) -> dict[str, list[dict[str, Any]]]:
    report_definitions = _wrapper_definitions(
        text,
        source,
        (("function", REPORT_WRAPPER_RE), ("arrow", REPORT_ARROW_WRAPPER_RE)),
        "report_wrapper_definition",
    )
    mower_set_definitions = _wrapper_definitions(
        text,
        source,
        (("function", MOWER_SET_WRAPPER_RE), ("arrow", MOWER_SET_ARROW_WRAPPER_RE)),
        "mower_set_wrapper_definition",
    )
    return {
        "report_wrapper_definitions": report_definitions,
        "report_callsite_contexts": _named_callsite_contexts(
            text, source, report_definitions, "report_wrapper_callsite"
        ),
        "report_field_contexts": _contexts_for_terms(
            text, source, REPORT_TARGETS, "report_response_fields", max_per_term=3
        ),
        "mower_set_wrapper_definitions": mower_set_definitions,
        "mower_set_callsite_contexts": _named_callsite_contexts(
            text, source, mower_set_definitions, "maintenance_mower_set_callsite"
        ),
    }



def _ui_key_candidates(text: str) -> list[str]:
    keywords = (
        "maintenance",
        "parts",
        "blade",
        "knife",
        "chassis",
        "replace",
        "replacement",
        "clean",
        "remaining",
        "used",
    )
    rows: list[str] = []
    for value in QUOTED_STRING_RE.findall(text):
        lower = value.lower()
        if not any(keyword in lower for keyword in keywords):
            continue
        compact = re.sub(r"\s+", " ", value).strip()
        if compact and compact not in rows:
            rows.append(compact)
        if len(rows) >= 96:
            break
    return rows


def _source_map_url(text: str, asset_url: str) -> str:
    matches = SOURCE_MAP_RE.findall(text)
    if not matches:
        return ""
    raw = str(matches[-1]).strip().strip("'\"")
    if not raw or raw.startswith("data:"):
        return ""
    return _safe_url(urllib.parse.urljoin(asset_url, raw))


def _source_map_priority(text: str, asset_url: str) -> int:
    lower = text.lower()
    score = _filename_bonus(asset_url)
    if "handleh5mowerset" in lower:
        score += 700
    if any(term.lower() in lower for term in MAINTENANCE_UI_TARGETS):
        score += 560
    if any(endpoint.lower() in lower for endpoint in REPORT_ENDPOINTS):
        score += 500
    if any(term.lower() in lower for term in REPORT_TRANSPORT_TARGETS):
        score += 340
    if "repair" in lower:
        score += 180
    return score


def _source_map_findings(map_text: str, map_url: str, asset_url: str) -> dict[str, Any]:
    try:
        payload = json.loads(map_text)
    except Exception as err:  # noqa: BLE001 - diagnostics-only public source map
        return {
            "map_url": _safe_url(map_url),
            "asset_url": _safe_url(asset_url),
            "parse_error": sanitize(f"{type(err).__name__}: {err}"),
        }
    if not isinstance(payload, dict):
        return {
            "map_url": _safe_url(map_url),
            "asset_url": _safe_url(asset_url),
            "parse_error": "source map root is not an object",
        }
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    contents = (
        payload.get("sourcesContent")
        if isinstance(payload.get("sourcesContent"), list)
        else []
    )
    target_terms = (
        MAINTENANCE_UI_TARGETS
        + MAINTENANCE_TARGETS
        + REPORT_ENDPOINTS
        + REPORT_TRANSPORT_TARGETS
        + ("handleH5MowerSet", "repair", "knife", "chassis")
    )
    matching_sources: list[dict[str, Any]] = []
    for index, source_name in enumerate(sources[:600]):
        if index >= len(contents) or not isinstance(contents[index], str):
            continue
        content = contents[index]
        lower = content.lower()
        matched = [term for term in target_terms if term.lower() in lower]
        if not matched:
            continue
        contexts: list[dict[str, str]] = []
        for term in matched[:8]:
            context = _context_around(content, term, radius=1400)
            if context:
                contexts.append({"term": term, "context": context})
        matching_sources.append(
            {
                "source_name": sanitize(str(source_name)),
                "matched_terms": matched[:32],
                "ui_key_candidates": _ui_key_candidates(content)[:48],
                "object_keys": _object_keys(content)[:80],
                "contexts": sanitize(contexts[:12]),
            }
        )
        if len(matching_sources) >= MAX_SOURCE_MAP_MATCHING_SOURCES:
            break
    return {
        "map_url": _safe_url(map_url),
        "asset_url": _safe_url(asset_url),
        "source_count": len(sources),
        "sources_content_count": len(contents),
        "source_names": sanitize([str(value) for value in sources[:160]]),
        "matching_sources": matching_sources,
    }

def _is_targeted_candidate(candidate: dict[str, Any]) -> bool:
    url = str(candidate.get("url") or "")
    theme_terms = {str(value).lower() for value in candidate.get("theme_terms") or []}
    return (
        _filename_bonus(url) >= 130
        or int(candidate.get("score") or 0) >= 180
        or any(term in theme_terms for term in TARGETED_THEME_TERMS)
    )

def _filename_bonus(url: str) -> int:
    name = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1].lower()
    rules = (
        ("maintenance", 320),
        ("repair", 310),
        ("parts", 300),
        ("blade", 280),
        ("knife", 280),
        ("chassis", 275),
        ("report", 250),
        ("request-", 240),
        ("native-", 230),
        ("component", 220),
        ("service-", 190),
        ("mower", 150),
        ("setting", 130),
    )
    return max((score for token, score in rules if token in name), default=0)


def _candidate_score(
    text: str,
    match: re.Match[str],
    url: str,
) -> tuple[int, list[str]]:
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
    score = _filename_bonus(url)
    score += min(120, len(terms) * 20)
    if any(term in terms for term in TARGETED_THEME_TERMS):
        score += 240
    if any(endpoint.lower() in nearby for endpoint in REPORT_ENDPOINTS):
        score += 360
    if any(term.lower() in nearby for term in MAINTENANCE_TARGETS):
        score += 180
    if "handleh5mowerset" in nearby:
        score += 220
    if "skipencryption" in nearby or "needrawresponse" in nearby:
        score += 80
    return score, terms


def _js_candidates(
    text: str,
    base_url: str,
    allowed_hosts: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for order, match in enumerate(JS_RE.finditer(text)):
        url = _resolve_js_url(base_url, match.group(1))
        parsed = urllib.parse.urlsplit(url)
        if (
            not url
            or parsed.scheme != "https"
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
                "basename": parsed.path.rsplit("/", 1)[-1],
                "score": score,
                "theme_terms": terms,
                "order": order,
            }
        )
    rows.sort(
        key=lambda row: (-int(row["score"]), int(row["order"]), str(row["url"]))
    )
    return rows


def probe_maintenance_h5(client: Any) -> dict[str, Any]:
    """Recover public H5 contracts for Maintenance and Mowing Reports."""
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
                asset = _resolve_js_url(url, value)
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
    sequence = 0
    best_scores: dict[str, int] = {}
    for url in root_scripts:
        if url in best_scores:
            continue
        best_scores[url] = 10_000
        heapq.heappush(queue, (-10_000, sequence, url, "root_script"))
        sequence += 1

    assets: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []
    maintenance_contexts: list[dict[str, Any]] = []
    maintenance_ui_contexts: list[dict[str, Any]] = []
    report_contexts: list[dict[str, Any]] = []
    report_transport_contexts: list[dict[str, Any]] = []
    request_shape_contexts: list[dict[str, Any]] = []
    bridge_call_contexts: list[dict[str, Any]] = []
    request_candidates: list[dict[str, Any]] = []
    bridge_candidates: list[dict[str, Any]] = []
    js_candidates: dict[str, dict[str, Any]] = {}
    fetched: set[str] = set()
    request_markers: set[tuple[str, str]] = set()
    bridge_markers: set[tuple[str, str, str]] = set()
    report_endpoints_found: set[str] = set()
    report_wrapper_definitions: list[dict[str, Any]] = []
    report_callsite_contexts: list[dict[str, Any]] = []
    report_field_contexts: list[dict[str, Any]] = []
    mower_set_wrapper_definitions: list[dict[str, Any]] = []
    mower_set_callsite_contexts: list[dict[str, Any]] = []
    targeted_fetches: list[dict[str, Any]] = []
    source_map_candidates: dict[str, dict[str, Any]] = {}
    source_map_fetches: list[dict[str, Any]] = []
    source_map_findings: list[dict[str, Any]] = []
    successful_assets = 0
    request_count = 0

    while queue and successful_assets < MAX_ASSETS and request_count < MAX_REQUESTS:
        neg_score, _, url, reason = heapq.heappop(queue)
        if url in fetched:
            continue
        fetched.add(url)
        request_count += 1
        result = _fetch(url, MAX_JS)
        text = str(result.get("_text") or "")
        row = _public(result)
        row["kind"] = "asset"
        row["discovery_reason"] = reason
        row["candidate_score"] = -neg_score if neg_score != -10_000 else None
        counts_toward_limit = bool(result.get("ok") and text)
        row["counts_toward_asset_limit"] = counts_toward_limit
        if counts_toward_limit:
            successful_assets += 1

        if not counts_toward_limit:
            row["matched_terms"] = []
            row["endpoint_paths"] = []
            row["http_methods"] = []
            row["skip_encryption"] = []
            row["request_shape_markers"] = []
            row["object_keys"] = []
            row["bridge_calls"] = []
            row["js_reference_count"] = 0
            assets.append(row)
            continue

        structure = _structure(text)
        row.update(structure)
        row["ui_key_candidates"] = _ui_key_candidates(text)
        map_url = _source_map_url(text, url)
        if map_url:
            parsed_map = urllib.parse.urlsplit(map_url)
            if (
                parsed_map.scheme == "https"
                and parsed_map.netloc in allowed_hosts
            ):
                map_score = _source_map_priority(text, url)
                row["source_map_url"] = map_url
                previous_map = source_map_candidates.get(map_url)
                if previous_map is None or map_score > int(previous_map["score"]):
                    source_map_candidates[map_url] = {
                        "url": map_url,
                        "asset_url": _safe_url(url),
                        "score": map_score,
                    }

        found_maintenance = _contexts_for_terms(
            text, url, MAINTENANCE_TARGETS, "maintenance", max_per_term=2
        )
        found_reports = _contexts_for_terms(
            text, url, REPORT_ENDPOINTS, "mowing_reports", max_per_term=3
        )
        found_request_shape = _contexts_for_terms(
            text,
            url,
            REQUEST_SHAPE_TERMS,
            "request_infrastructure",
            max_per_term=2,
        )
        found_maintenance_ui = _contexts_for_terms(
            text,
            url,
            MAINTENANCE_UI_TARGETS,
            "parts_maintenance_ui",
            max_per_term=3,
        )
        found_report_transport = _contexts_for_terms(
            text,
            url,
            REPORT_TRANSPORT_TARGETS,
            "report_transport",
            max_per_term=3,
        )
        callsite_findings = _callsite_findings(text, url)
        report_wrapper_definitions.extend(callsite_findings["report_wrapper_definitions"])
        report_callsite_contexts.extend(callsite_findings["report_callsite_contexts"])
        report_field_contexts.extend(callsite_findings["report_field_contexts"])
        mower_set_wrapper_definitions.extend(callsite_findings["mower_set_wrapper_definitions"])
        mower_set_callsite_contexts.extend(callsite_findings["mower_set_callsite_contexts"])

        maintenance_contexts.extend(found_maintenance)
        maintenance_ui_contexts.extend(found_maintenance_ui)
        report_contexts.extend(found_reports)
        report_transport_contexts.extend(found_report_transport)
        request_shape_contexts.extend(found_request_shape)
        bridge_call_contexts.extend(
            [
                item
                for item in found_request_shape
                if item["term"]
                in ("handleH5MowerSet", "handleEncipherment", "handleDecrypt")
            ]
        )
        contexts.extend(found_maintenance)
        contexts.extend(found_reports)

        for path in structure["endpoint_paths"]:
            marker = (url, path)
            if marker in request_markers:
                continue
            request_markers.add(marker)
            nearby = _context_around(text, path)
            focus = "supporting"
            if path in REPORT_ENDPOINTS:
                focus = "mowing_reports"
                report_endpoints_found.add(path)
            elif "maintenance" in path.lower():
                focus = "maintenance"
            request_candidates.append(
                {
                    "focus": focus,
                    "source": _safe_url(url),
                    "path": path,
                    **(_structure(nearby) if nearby else {}),
                    "context": nearby,
                }
            )
            if len(request_candidates) >= MAX_REQUEST_CANDIDATES:
                break

        for bridge in structure["bridge_calls"]:
            marker = (url, bridge["callee"], bridge["method"])
            if marker in bridge_markers:
                continue
            bridge_markers.add(marker)
            nearby = _context_around(text, bridge["method"])
            bridge_candidates.append(
                {
                    "source": _safe_url(url),
                    **bridge,
                    **(_structure(nearby) if nearby else {}),
                    "context": nearby,
                }
            )

        discovered = _js_candidates(text, url, allowed_hosts)
        row["js_reference_count"] = len(discovered)
        for candidate in discovered:
            candidate_url = str(candidate["url"])
            previous = js_candidates.get(candidate_url)
            if previous is None or int(candidate["score"]) > int(previous["score"]):
                js_candidates[candidate_url] = candidate
            new_score = int(candidate["score"])
            if candidate_url in fetched:
                continue
            if new_score <= best_scores.get(candidate_url, -1):
                continue
            best_scores[candidate_url] = new_score
            heapq.heappush(
                queue,
                (-new_score, sequence, candidate_url, "bounded_lazy_chunk"),
            )
            sequence += 1

        assets.append(row)

    targeted_queue: list[tuple[int, int, str, dict[str, Any]]] = []
    targeted_queued: set[str] = set()
    targeted_sequence = 0
    for candidate in js_candidates.values():
        candidate_url = str(candidate["url"])
        if candidate_url in fetched or not _is_targeted_candidate(candidate):
            continue
        targeted_queued.add(candidate_url)
        heapq.heappush(
            targeted_queue,
            (-int(candidate["score"]), targeted_sequence, candidate_url, candidate),
        )
        targeted_sequence += 1

    targeted_success = 0
    while (
        targeted_queue
        and targeted_success < MAX_TARGETED_ASSETS
        and request_count < MAX_REQUESTS
    ):
        neg_score, _, url, candidate = heapq.heappop(targeted_queue)
        if url in fetched:
            continue
        fetched.add(url)
        request_count += 1
        result = _fetch(url, MAX_JS)
        text = str(result.get("_text") or "")
        row = _public(result)
        row["kind"] = "asset"
        row["discovery_reason"] = "targeted_priority_reserve"
        row["candidate_score"] = -neg_score
        counts_toward_limit = bool(result.get("ok") and text)
        row["counts_toward_asset_limit"] = counts_toward_limit
        targeted_record: dict[str, Any] = {
            "url": _safe_url(url),
            "basename": candidate.get("basename"),
            "candidate_score": -neg_score,
            "ok": bool(result.get("ok")),
            "http_status": result.get("http_status"),
        }
        if not counts_toward_limit:
            row["matched_terms"] = []
            row["endpoint_paths"] = []
            row["http_methods"] = []
            row["skip_encryption"] = []
            row["request_shape_markers"] = []
            row["object_keys"] = []
            row["bridge_calls"] = []
            row["js_reference_count"] = 0
            targeted_fetches.append(targeted_record)
            assets.append(row)
            continue

        successful_assets += 1
        targeted_success += 1
        structure = _structure(text)
        row.update(structure)
        row["ui_key_candidates"] = _ui_key_candidates(text)
        map_url = _source_map_url(text, url)
        if map_url:
            parsed_map = urllib.parse.urlsplit(map_url)
            if (
                parsed_map.scheme == "https"
                and parsed_map.netloc in allowed_hosts
            ):
                map_score = _source_map_priority(text, url)
                row["source_map_url"] = map_url
                previous_map = source_map_candidates.get(map_url)
                if previous_map is None or map_score > int(previous_map["score"]):
                    source_map_candidates[map_url] = {
                        "url": map_url,
                        "asset_url": _safe_url(url),
                        "score": map_score,
                    }
        targeted_record["matched_terms"] = structure["matched_terms"]
        targeted_record["endpoint_paths"] = structure["endpoint_paths"]

        found_maintenance = _contexts_for_terms(
            text, url, MAINTENANCE_TARGETS, "maintenance", max_per_term=2
        )
        found_reports = _contexts_for_terms(
            text, url, REPORT_ENDPOINTS, "mowing_reports", max_per_term=3
        )
        found_request_shape = _contexts_for_terms(
            text,
            url,
            REQUEST_SHAPE_TERMS,
            "request_infrastructure",
            max_per_term=2,
        )
        found_maintenance_ui = _contexts_for_terms(
            text,
            url,
            MAINTENANCE_UI_TARGETS,
            "parts_maintenance_ui",
            max_per_term=3,
        )
        found_report_transport = _contexts_for_terms(
            text,
            url,
            REPORT_TRANSPORT_TARGETS,
            "report_transport",
            max_per_term=3,
        )
        maintenance_contexts.extend(found_maintenance)
        maintenance_ui_contexts.extend(found_maintenance_ui)
        report_contexts.extend(found_reports)
        report_transport_contexts.extend(found_report_transport)
        request_shape_contexts.extend(found_request_shape)
        bridge_call_contexts.extend(
            [
                item
                for item in found_request_shape
                if item["term"]
                in ("handleH5MowerSet", "handleEncipherment", "handleDecrypt")
            ]
        )

        callsite_findings = _callsite_findings(text, url)
        report_wrapper_definitions.extend(callsite_findings["report_wrapper_definitions"])
        report_callsite_contexts.extend(callsite_findings["report_callsite_contexts"])
        report_field_contexts.extend(callsite_findings["report_field_contexts"])
        mower_set_wrapper_definitions.extend(callsite_findings["mower_set_wrapper_definitions"])
        mower_set_callsite_contexts.extend(callsite_findings["mower_set_callsite_contexts"])

        for path in structure["endpoint_paths"]:
            marker = (url, path)
            if marker in request_markers:
                continue
            request_markers.add(marker)
            nearby = _context_around(text, path)
            focus = "supporting"
            if path in REPORT_ENDPOINTS:
                focus = "mowing_reports"
                report_endpoints_found.add(path)
            elif "maintenance" in path.lower():
                focus = "maintenance"
            request_candidates.append(
                {
                    "focus": focus,
                    "source": _safe_url(url),
                    "path": path,
                    **(_structure(nearby) if nearby else {}),
                    "context": nearby,
                }
            )

        discovered = _js_candidates(text, url, allowed_hosts)
        row["js_reference_count"] = len(discovered)
        for child in discovered:
            child_url = str(child["url"])
            previous = js_candidates.get(child_url)
            if previous is None or int(child["score"]) > int(previous["score"]):
                js_candidates[child_url] = child
            if (
                child_url in fetched
                or child_url in targeted_queued
                or not _is_targeted_candidate(child)
            ):
                continue
            targeted_queued.add(child_url)
            heapq.heappush(
                targeted_queue,
                (-int(child["score"]), targeted_sequence, child_url, child),
            )
            targeted_sequence += 1

        targeted_fetches.append(targeted_record)
        assets.append(row)

    source_map_success = 0
    source_map_rows = sorted(
        source_map_candidates.values(),
        key=lambda row: (-int(row["score"]), str(row["url"])),
    )
    for map_candidate in source_map_rows[:MAX_SOURCE_MAPS]:
        if request_count >= MAX_REQUESTS:
            break
        map_url = str(map_candidate["url"])
        request_count += 1
        result = _fetch(map_url, MAX_SOURCE_MAP)
        map_text = str(result.get("_text") or "")
        fetch_row = _public(result)
        fetch_row["asset_url"] = map_candidate["asset_url"]
        fetch_row["candidate_score"] = map_candidate["score"]
        parsed_ok = bool(result.get("ok") and map_text and not result.get("truncated"))
        fetch_row["parsed"] = parsed_ok
        if parsed_ok:
            source_map_success += 1
            findings = _source_map_findings(
                map_text,
                map_url,
                str(map_candidate["asset_url"]),
            )
            source_map_findings.append(findings)
            fetch_row["matching_source_count"] = len(
                findings.get("matching_sources") or []
            )
        source_map_fetches.append(fetch_row)

    candidate_rows = sorted(
        js_candidates.values(),
        key=lambda row: (-int(row["score"]), int(row["order"]), str(row["url"])),
    )
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
        "focus": ["maintenance", "mowing_reports"],
        "entry_paths": list(ENTRY_PATHS),
        "maintenance_targets": list(MAINTENANCE_TARGETS),
        "maintenance_ui_targets": list(MAINTENANCE_UI_TARGETS),
        "report_endpoints": list(REPORT_ENDPOINTS),
        "report_targets": list(REPORT_TARGETS),
        "report_transport_targets": list(REPORT_TRANSPORT_TARGETS),
        "report_endpoints_found": sorted(report_endpoints_found),
        "request_shape_terms": list(REQUEST_SHAPE_TERMS),
        "priority_chunk_name_patterns": list(PRIORITY_FILENAME_TOKENS),
        "limits": {
            "timeout_seconds_per_request": TIMEOUT,
            "max_html_bytes": MAX_HTML,
            "max_js_bytes_per_asset": MAX_JS,
            "max_broad_successful_assets": MAX_ASSETS,
            "max_targeted_successful_assets": MAX_TARGETED_ASSETS,
            "max_total_successful_assets": MAX_ASSETS + MAX_TARGETED_ASSETS,
            "max_requests": MAX_REQUESTS,
            "max_contexts": MAX_CONTEXTS,
            "max_request_candidates": MAX_REQUEST_CANDIDATES,
            "max_js_candidates_in_output": MAX_JS_CANDIDATES,
            "small_json_max_bytes": SMALL_JSON_MAX,
            "max_source_maps": MAX_SOURCE_MAPS,
            "max_source_map_bytes": MAX_SOURCE_MAP,
            "max_source_map_matching_sources": MAX_SOURCE_MAP_MATCHING_SOURCES,
        },
        "credential_safety": (
            "No token, cookie, uid, device id, mower serial or encrypted p:101 "
            "business payload is sent to H5. Only public GET resources are read."
        ),
        "investigation_goal": (
            "Trace Parts maintenance from UI/i18n/source-map evidence through the "
            "Replacement done and Clean now handlers to the official write contract, "
            "while recovering the remaining H5 encryption/transport layer for the "
            "already identified read-only Mowing Reports contracts."
        ),
        "live_report_request_executed": False,
        "report_transport_assessment": {
            "status": "not_assumed",
            "h5_observed_outer_shape": "body.data after native handleEncipherment",
            "private_cloud_observed_outer_shape": "p:101 envelope fields d,h,k,p,t",
            "reason": (
                "The observable envelope shapes differ, so beta6 does not guess that "
                "private-cloud p:101 is interchangeable with the H5 native bridge."
            ),
        },
        "pages": pages,
        "assets": assets,
        "contexts": contexts[:MAX_CONTEXTS],
        "maintenance_contexts": maintenance_contexts[:MAX_CONTEXTS],
        "maintenance_ui_contexts": maintenance_ui_contexts[:MAX_CONTEXTS],
        "report_contexts": report_contexts[:MAX_CONTEXTS],
        "report_transport_contexts": report_transport_contexts[:MAX_CONTEXTS],
        "request_shape_contexts": request_shape_contexts[:MAX_CONTEXTS],
        "source_map_fetches": source_map_fetches,
        "source_map_findings": source_map_findings,
        "bridge_call_contexts": bridge_call_contexts[:MAX_CONTEXTS],
        "report_wrapper_definitions": report_wrapper_definitions[:64],
        "report_callsite_contexts": report_callsite_contexts[:96],
        "report_field_contexts": report_field_contexts[:96],
        "mower_set_wrapper_definitions": mower_set_wrapper_definitions[:64],
        "mower_set_callsite_contexts": mower_set_callsite_contexts[:96],
        "targeted_fetches": targeted_fetches,
        "request_candidates": request_candidates[:MAX_REQUEST_CANDIDATES],
        "bridge_candidates": bridge_candidates[:96],
        "js_discovery": {
            "strategy": "semantic_hash_agnostic_priority+targeted_callsite_recovery+ui_source_map_recovery",
            "candidate_count": len(candidate_rows),
            "candidates": candidate_rows[:MAX_JS_CANDIDATES],
            "fetched_count": len(fetched),
            "successful_asset_count": successful_assets,
            "request_count": request_count,
            "failed_request_count": request_count - successful_assets - source_map_success,
            "targeted_fetch_count": len(targeted_fetches),
            "targeted_success_count": targeted_success,
            "source_map_candidate_count": len(source_map_rows),
            "source_map_fetch_count": len(source_map_fetches),
            "source_map_success_count": source_map_success,
            "unfetched_candidates": unfetched,
        },
        "note": (
            "0.4.3-beta6 steps back to Parts maintenance UI/i18n/route/source-map "
            "recovery, fixes default-argument handleH5MowerSet wrapper detection, and "
            "keeps Mowing Reports focused on transport proof. It remains read-only and "
            "executes no report API request, maintenance mutation or mower command."
        ),
    }
