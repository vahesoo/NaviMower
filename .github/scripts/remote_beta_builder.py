from __future__ import annotations

import json
from pathlib import Path

ROOT = Path.cwd()
COMPONENT = ROOT / "custom_components" / "navimower"

manifest_path = COMPONENT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != "0.4.3-beta3":
    raise SystemExit(f"Expected 0.4.3-beta3 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta4"
manifest_path.write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

discovery = r'''"""Read-only public H5 contract discovery for Maintenance and Mowing Reports."""
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

TARGET_TERMS = MAINTENANCE_TARGETS + REPORT_ENDPOINTS + REPORT_TARGETS
REQUEST_SHAPE_TERMS = (
    "handleH5MowerSet",
    "skipEncryption",
    "needRawResponse",
    "handleEncipherment",
    "handleDecrypt",
)
THEME_TERMS = (
    "maintenance",
    "blade",
    "knife",
    "repair",
    "report",
    "mowing",
    "duration",
)
PRIORITY_FILENAME_TOKENS = (
    "maintenance",
    "blade",
    "knife",
    "report",
    "request-",
    "native-",
    "service-",
    "mower",
    "setting",
)

MAX_HTML = 256 * 1024
MAX_JS = 2 * 1024 * 1024
MAX_ASSETS = 48
MAX_REQUESTS = 96
MAX_CONTEXTS = 96
MAX_REQUEST_CANDIDATES = 160
MAX_JS_CANDIDATES = 160
MAX_UNFETCHED_CANDIDATES = 80
SMALL_JSON_MAX = 8192
CONTEXT_RADIUS = 2200
CANDIDATE_RADIUS = 1500
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
            "User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.3-beta4",
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


def _filename_bonus(url: str) -> int:
    name = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1].lower()
    rules = (
        ("maintenance", 280),
        ("blade", 260),
        ("knife", 260),
        ("report", 250),
        ("request-", 240),
        ("native-", 230),
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
    report_contexts: list[dict[str, Any]] = []
    request_shape_contexts: list[dict[str, Any]] = []
    bridge_call_contexts: list[dict[str, Any]] = []
    request_candidates: list[dict[str, Any]] = []
    bridge_candidates: list[dict[str, Any]] = []
    js_candidates: dict[str, dict[str, Any]] = {}
    fetched: set[str] = set()
    request_markers: set[tuple[str, str]] = set()
    bridge_markers: set[tuple[str, str, str]] = set()
    report_endpoints_found: set[str] = set()
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
        maintenance_contexts.extend(found_maintenance)
        report_contexts.extend(found_reports)
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
        "report_endpoints": list(REPORT_ENDPOINTS),
        "report_targets": list(REPORT_TARGETS),
        "report_endpoints_found": sorted(report_endpoints_found),
        "request_shape_terms": list(REQUEST_SHAPE_TERMS),
        "priority_chunk_name_patterns": list(PRIORITY_FILENAME_TOKENS),
        "limits": {
            "timeout_seconds_per_request": TIMEOUT,
            "max_html_bytes": MAX_HTML,
            "max_js_bytes_per_asset": MAX_JS,
            "max_successful_assets": MAX_ASSETS,
            "max_requests": MAX_REQUESTS,
            "max_contexts": MAX_CONTEXTS,
            "max_request_candidates": MAX_REQUEST_CANDIDATES,
            "max_js_candidates_in_output": MAX_JS_CANDIDATES,
            "small_json_max_bytes": SMALL_JSON_MAX,
        },
        "credential_safety": (
            "No token, cookie, uid, device id, mower serial or encrypted p:101 "
            "business payload is sent to H5. Only public GET resources are read."
        ),
        "investigation_goal": (
            "Recover official Maintenance & Tools write-contract structure for blade "
            "runtime reset and maintenance mode, plus read-only Mowing Reports "
            "contracts for day/week/month data and the main vehicle report."
        ),
        "pages": pages,
        "assets": assets,
        "contexts": contexts[:MAX_CONTEXTS],
        "maintenance_contexts": maintenance_contexts[:MAX_CONTEXTS],
        "report_contexts": report_contexts[:MAX_CONTEXTS],
        "request_shape_contexts": request_shape_contexts[:MAX_CONTEXTS],
        "bridge_call_contexts": bridge_call_contexts[:MAX_CONTEXTS],
        "request_candidates": request_candidates[:MAX_REQUEST_CANDIDATES],
        "bridge_candidates": bridge_candidates[:96],
        "js_discovery": {
            "strategy": "semantic_hash_agnostic_priority",
            "candidate_count": len(candidate_rows),
            "candidates": candidate_rows[:MAX_JS_CANDIDATES],
            "fetched_count": len(fetched),
            "successful_asset_count": successful_assets,
            "request_count": request_count,
            "failed_request_count": request_count - successful_assets,
            "unfetched_candidates": unfetched,
        },
        "note": (
            "0.4.3-beta4 is a bounded read-only contract-recovery pass. Hashed chunk "
            "suffixes are treated as build artifacts; semantic chunk prefixes and "
            "import relationships drive priority. No maintenance mutation or mower "
            "command is executed."
        ),
    }
'''
(COMPONENT / "maintenance_h5_discovery.py").write_text(discovery, encoding="utf-8")

diagnostics_path = COMPONENT / "diagnostics.py"
diagnostics = diagnostics_path.read_text(encoding="utf-8")
diagnostics = diagnostics.replace("0.4.3-beta3", "0.4.3-beta4")
diagnostics = diagnostics.replace(
    "Maintenance & Tools lazy chunks and request structure; no mutation runs.",
    "Maintenance + Mowing Reports contracts and request structure; no mutation runs.",
)
diagnostics = diagnostics.replace(
    "broadens bounded public H5 Maintenance & Tools lazy-chunk discovery.",
    "recovers bounded public H5 Maintenance + Mowing Reports contracts.",
)
diagnostics_path.write_text(diagnostics, encoding="utf-8")

beta3_test = ROOT / "tests" / "test_v043_beta3.py"
beta3 = beta3_test.read_text(encoding="utf-8")
beta3 = beta3.replace(
    'assert manifest["version"] == "0.4.3-beta3"',
    'assert manifest["version"].startswith("0.4.3")',
)
beta3 = beta3.replace(
    'assert "0.4.3-beta3" in diagnostics',
    'assert "0.4.3-beta" in diagnostics',
)
beta3_test.write_text(beta3, encoding="utf-8")

test_source = r'''"""Regression contracts for Navimower 0.4.3-beta4 H5 contract recovery."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta4_version_notes_and_focus() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta4"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta4.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta4")
    assert "Maintenance + Mowing Reports" in notes
    assert "content-hash" in notes
    assert "strictly read-only" in notes


def test_beta4_discovery_fixes_duplicate_asset_paths() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    assert '"/static/js/static/js/"' in source
    assert '"/assets/assets/"' in source
    assert "def _resolve_js_url" in source
    assert 'for marker in ("static/js/", "assets/")' in source
    assert "MAX_ASSETS = 48" in source
    assert "MAX_REQUESTS = 96" in source
    assert "successful_assets < MAX_ASSETS" in source
    assert 'row["counts_toward_asset_limit"] = counts_toward_limit' in source


def test_beta4_targets_report_contracts_and_hash_agnostic_chunks() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    for phrase in (
        "/vehicle/report/get-day-week-month-data",
        "/vehicle/report/vehicle-main-report",
        "handleH5MowerSet",
        "skipEncryption",
        "needRawResponse",
        '"request-"',
        '"native-"',
        '"service-"',
        '"report_contexts": report_contexts',
        '"request_shape_contexts": request_shape_contexts',
        '"bridge_call_contexts": bridge_call_contexts',
        '"report_endpoints_found": sorted(report_endpoints_found)',
        '"strategy": "semantic_hash_agnostic_priority"',
    ):
        assert phrase in source


def test_beta4_regexes_compile_and_probe_remains_non_mutating() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    patterns: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
            and node.func.attr == "compile"
        ):
            continue
        pattern = ast.literal_eval(node.args[0])
        assert isinstance(pattern, str)
        re.compile(pattern)
        patterns.append(pattern)
    assert len(patterns) >= 7
    assert 'method="GET"' in source
    assert '"mutation_calls_executed": False' in source
    assert '"public_unauthenticated_h5_only": True' in source
    assert "client.call(" not in source
    assert "Authorization" not in source
    assert "Cookie" not in source


def test_beta4_diagnostics_describes_both_contract_families() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert "probe_maintenance_h5" in diagnostics
    assert "0.4.3-beta4" in diagnostics
    assert "Maintenance + Mowing Reports" in diagnostics
    assert '"maintenance_h5_discovery": maintenance_h5_discovery' in diagnostics
'''
(ROOT / "tests" / "test_v043_beta4.py").write_text(test_source, encoding="utf-8")

release_notes = '''title: Navimower 0.4.3-beta4

Navimower 0.4.3-beta4 changes the temporary public-H5 investigation from a Maintenance-only crawl into a focused **Maintenance + Mowing Reports contract recovery** pass.

### Hash-agnostic H5 chunk discovery

- H5 JavaScript filenames such as `native-d66fe239.js` and `request-e9a0ef42.js` contain a bundler content-hash suffix. Beta4 therefore prioritizes semantic chunk prefixes and import relationships instead of depending on one exact hash.
- `native-*`, `request-*`, `service-*`, report, maintenance, blade, knife, mower and setting chunks are promoted ahead of generic `index-*` chunks.
- Fix duplicate relative-path resolution that previously produced `/static/js/static/js/...` and `/assets/assets/...` 404 requests.
- Failed asset requests no longer consume the 48-successful-JavaScript-asset budget; a separate bounded request cap remains in place.

### Mowing Reports recovery

- Capture focused contexts around `/vehicle/report/get-day-week-month-data` and `/vehicle/report/vehicle-main-report`.
- Capture nearby request payload keys, HTTP methods, `skipEncryption`, `needRawResponse` and related request-wrapper structure.
- Preserve dedicated report endpoint findings and report contexts so the next beta can implement proven read-only report calls instead of guessing response fields.

### Maintenance recovery

- Continue looking for blade-runtime reset and maintenance-mode contracts.
- Capture call-site context for `handleH5MowerSet`, `handleEncipherment`, `handleDecrypt` and request-wrapper markers instead of recording only bridge method names.

### Safety

Beta4 remains **strictly read-only** and Download-diagnostics-only. It performs only bounded public HTTPS GET requests, sends no mower/account identifiers to H5, and executes no blade reset, maintenance-mode command, cutting-height change or other mower mutation.
'''
release_path = ROOT / ".github" / "release-notes" / "0.4.3-beta4.md"
release_path.write_text(release_notes, encoding="utf-8")

changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
entry = '''## 0.4.3-beta4

Focused Maintenance + Mowing Reports public-H5 contract recovery.

### Changed

- Fix lazy-chunk URL canonicalization so duplicate `static/js/static/js` and `assets/assets` paths do not waste the crawl budget.
- Count only successful JavaScript fetches toward the 48-asset limit while keeping a separate bounded request limit.
- Prioritize semantic hash-agnostic `native-*`, `request-*`, `service-*`, report, maintenance, blade and knife chunks.
- Capture dedicated contexts for the day/week/month report and vehicle main report endpoints, plus request/encryption/native-bridge call sites.

### Safety

- Discovery remains Download-diagnostics-only, public, unauthenticated and GET-only.
- No maintenance counter reset, maintenance mode, cutting-height mutation or mower command is executed.

'''
prefix = "# Changelog\n\n"
if not changelog.startswith(prefix):
    raise SystemExit("Unexpected CHANGELOG header")
changelog_path.write_text(prefix + entry + changelog[len(prefix):], encoding="utf-8")
