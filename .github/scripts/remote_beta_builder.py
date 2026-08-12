from __future__ import annotations

import json
from pathlib import Path

ROOT = Path.cwd()
COMPONENT = ROOT / "custom_components" / "navimower"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one marker, found {count}")
    return text.replace(old, new, 1)


def replace_count(text: str, old: str, new: str, expected: int, label: str) -> str:
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{label}: expected {expected} markers, found {count}")
    return text.replace(old, new)


manifest_path = COMPONENT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != "0.4.3-beta5":
    raise SystemExit(f"Expected 0.4.3-beta5 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta6"
manifest_path.write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

discovery_path = COMPONENT / "maintenance_h5_discovery.py"
source = discovery_path.read_text(encoding="utf-8")
source = replace_once(
    source,
    '"User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.3-beta5",',
    '"User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.3-beta6",',
    "discovery user agent",
)

source = replace_once(
    source,
    '''    "usedTime",
    "setTime",
)

REPORT_ENDPOINTS = (
''',
    '''    "usedTime",
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
''',
    "maintenance UI targets",
)

source = replace_once(
    source,
    '''    "totalMowingArea",
    "totalMowingTime",
)

TARGET_TERMS = MAINTENANCE_TARGETS + REPORT_ENDPOINTS + REPORT_TARGETS
''',
    '''    "totalMowingArea",
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
    '\"p\":\"101\"',
)

TARGET_TERMS = (
    MAINTENANCE_TARGETS
    + MAINTENANCE_UI_TARGETS
    + REPORT_ENDPOINTS
    + REPORT_TARGETS
)
''',
    "report transport targets",
)

source = replace_once(
    source,
    '''THEME_TERMS = (
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
''',
    '''THEME_TERMS = (
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
''',
    "maintenance theme expansion",
)

source = replace_once(
    source,
    '''MAX_ASSETS = 48
MAX_TARGETED_ASSETS = 16
MAX_REQUESTS = 128
MAX_CONTEXTS = 96
MAX_REQUEST_CANDIDATES = 160
MAX_JS_CANDIDATES = 160
MAX_UNFETCHED_CANDIDATES = 80
SMALL_JSON_MAX = 8192
''',
    '''MAX_ASSETS = 48
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
''',
    "beta6 discovery budgets",
)

source = replace_once(
    source,
    '''BRIDGE_RE = re.compile(
    r"(?P<callee>(?:[A-Za-z_$][\\w$]*\\.)*(?:sendEncryptionData|callNative|sendMessageToNative))"
    r"\\s*\\(\\s*[\\\"'](?P<method>[^\\\"']{1,160})[\\\"']",
    re.I,
)
REPORT_WRAPPER_RE = re.compile(
''',
    '''BRIDGE_RE = re.compile(
    r"(?P<callee>(?:[A-Za-z_$][\\w$]*\\.)*(?:sendEncryptionData|callNative|sendMessageToNative))"
    r"\\s*\\(\\s*[\\\"'](?P<method>[^\\\"']{1,160})[\\\"']",
    re.I,
)
SOURCE_MAP_RE = re.compile(r"sourceMappingURL\\s*=\\s*([^\\s*]+)", re.I)
QUOTED_STRING_RE = re.compile(r"[\\\"']([^\\\"'\\r\\n]{2,180})[\\\"']")
REPORT_WRAPPER_RE = re.compile(
''',
    "source map regexes",
)

source = replace_once(
    source,
    '''MOWER_SET_WRAPPER_RE = re.compile(
    r"function\\s+(?P<name>[A-Za-z_$][\\w$]*)\\s*\\(\\s*(?P<param>[A-Za-z_$][\\w$]*)\\s*\\)"
    r"\\s*\\{.{0,1800}?(?:callNative|sendMessageToNative)\\s*\\(\\s*[\\\"']handleH5MowerSet[\\\"']",
    re.I | re.S,
)
MOWER_SET_ARROW_WRAPPER_RE = re.compile(
    r"(?P<name>[A-Za-z_$][\\w$]*)\\s*=\\s*(?:async\\s*)?\\(?\\s*(?P<param>[A-Za-z_$][\\w$]*)\\s*\\)?"
    r"\\s*=>.{0,1800}?(?:callNative|sendMessageToNative)\\s*\\(\\s*[\\\"']handleH5MowerSet[\\\"']",
    re.I | re.S,
)
''',
    '''MOWER_SET_WRAPPER_RE = re.compile(
    r"function\\s+(?P<name>[A-Za-z_$][\\w$]*)\\s*\\(\\s*(?P<param>[A-Za-z_$][\\w$]*)"
    r"(?:\\s*=\\s*\\{\\})?\\s*\\)\\s*\\{[^{}]{0,700}?"
    r"(?:callNative|sendMessageToNative)\\s*\\(\\s*[\\\"']handleH5MowerSet[\\\"']",
    re.I | re.S,
)
MOWER_SET_ARROW_WRAPPER_RE = re.compile(
    r"(?P<name>[A-Za-z_$][\\w$]*)\\s*=\\s*(?:async\\s*)?\\(?\\s*(?P<param>[A-Za-z_$][\\w$]*)"
    r"(?:\\s*=\\s*\\{\\})?\\s*\\)?\\s*=>[^;]{0,700}?"
    r"(?:callNative|sendMessageToNative)\\s*\\(\\s*[\\\"']handleH5MowerSet[\\\"']",
    re.I | re.S,
)
''',
    "default-aware mower set wrappers",
)

source = replace_once(
    source,
    '''            maintenance_terms = [
                term
                for term in (
                    "maintenance",
                    "partsMaintenance",
                    "blade",
                    "knife",
                    "componentMaintenance",
                    "knifeDurationSet",
                    "chassisDurationSet",
                    "cutHeight",
                    "cuttingHeight",
                )
                if term.lower() in lower
            ]
''',
    '''            maintenance_terms = [
                term
                for term in (
                    MAINTENANCE_TARGETS
                    + MAINTENANCE_UI_TARGETS
                    + ("blade", "knife", "chassis", "repair")
                )
                if term.lower() in lower
            ]
''',
    "maintenance callsite semantics",
)

helper_marker = "\ndef _is_targeted_candidate(candidate: dict[str, Any]) -> bool:\n"
helpers = r'''

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
'''
source = replace_once(source, helper_marker, helpers + helper_marker, "source map helpers")

source = replace_once(
    source,
    '''def _is_targeted_candidate(candidate: dict[str, Any]) -> bool:
    url = str(candidate.get("url") or "")
    return _filename_bonus(url) >= 130 or int(candidate.get("score") or 0) >= 180
''',
    '''def _is_targeted_candidate(candidate: dict[str, Any]) -> bool:
    url = str(candidate.get("url") or "")
    theme_terms = {str(value).lower() for value in candidate.get("theme_terms") or []}
    return (
        _filename_bonus(url) >= 130
        or int(candidate.get("score") or 0) >= 180
        or any(term in theme_terms for term in TARGETED_THEME_TERMS)
    )
''',
    "theme-aware targeted candidate selection",
)

source = replace_once(
    source,
    '''    rules = (
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
''',
    '''    rules = (
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
''',
    "filename priority expansion",
)

source = replace_once(
    source,
    '''    score = _filename_bonus(url)
    score += min(120, len(terms) * 20)
''',
    '''    score = _filename_bonus(url)
    score += min(120, len(terms) * 20)
    if any(term in terms for term in TARGETED_THEME_TERMS):
        score += 240
''',
    "repair theme priority bonus",
)

source = replace_once(
    source,
    '''    maintenance_contexts: list[dict[str, Any]] = []
    report_contexts: list[dict[str, Any]] = []
    request_shape_contexts: list[dict[str, Any]] = []
''',
    '''    maintenance_contexts: list[dict[str, Any]] = []
    maintenance_ui_contexts: list[dict[str, Any]] = []
    report_contexts: list[dict[str, Any]] = []
    report_transport_contexts: list[dict[str, Any]] = []
    request_shape_contexts: list[dict[str, Any]] = []
''',
    "beta6 context collections",
)
source = replace_once(
    source,
    '''    targeted_fetches: list[dict[str, Any]] = []
    successful_assets = 0
    request_count = 0
''',
    '''    targeted_fetches: list[dict[str, Any]] = []
    source_map_candidates: dict[str, dict[str, Any]] = {}
    source_map_fetches: list[dict[str, Any]] = []
    source_map_findings: list[dict[str, Any]] = []
    successful_assets = 0
    request_count = 0
''',
    "source map collections",
)

source = replace_count(
    source,
    '''        structure = _structure(text)
        row.update(structure)
''',
    '''        structure = _structure(text)
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
''',
    2,
    "asset source map capture",
)

source = replace_count(
    source,
    '''        found_request_shape = _contexts_for_terms(
            text,
            url,
            REQUEST_SHAPE_TERMS,
            "request_infrastructure",
            max_per_term=2,
        )
''',
    '''        found_request_shape = _contexts_for_terms(
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
''',
    2,
    "UI and report transport contexts",
)

source = replace_count(
    source,
    '''        maintenance_contexts.extend(found_maintenance)
        report_contexts.extend(found_reports)
        request_shape_contexts.extend(found_request_shape)
''',
    '''        maintenance_contexts.extend(found_maintenance)
        maintenance_ui_contexts.extend(found_maintenance_ui)
        report_contexts.extend(found_reports)
        report_transport_contexts.extend(found_report_transport)
        request_shape_contexts.extend(found_request_shape)
''',
    2,
    "collect UI and transport contexts",
)

source_map_marker = '''    candidate_rows = sorted(
        js_candidates.values(),
'''
source_map_pass = r'''    source_map_success = 0
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
'''
source = replace_once(source, source_map_marker, source_map_pass, "source map fetch pass")

source = replace_once(
    source,
    '''        "maintenance_targets": list(MAINTENANCE_TARGETS),
        "report_endpoints": list(REPORT_ENDPOINTS),
        "report_targets": list(REPORT_TARGETS),
''',
    '''        "maintenance_targets": list(MAINTENANCE_TARGETS),
        "maintenance_ui_targets": list(MAINTENANCE_UI_TARGETS),
        "report_endpoints": list(REPORT_ENDPOINTS),
        "report_targets": list(REPORT_TARGETS),
        "report_transport_targets": list(REPORT_TRANSPORT_TARGETS),
''',
    "beta6 output target lists",
)

source = replace_once(
    source,
    '''            "small_json_max_bytes": SMALL_JSON_MAX,
        },
''',
    '''            "small_json_max_bytes": SMALL_JSON_MAX,
            "max_source_maps": MAX_SOURCE_MAPS,
            "max_source_map_bytes": MAX_SOURCE_MAP,
            "max_source_map_matching_sources": MAX_SOURCE_MAP_MATCHING_SOURCES,
        },
''',
    "source map limits metadata",
)

source = replace_once(
    source,
    '''        "investigation_goal": (
            "Recover official Maintenance & Tools write-contract structure for blade "
            "runtime reset and maintenance mode, plus read-only Mowing Reports "
            "contracts for day/week/month data and the main vehicle report."
        ),
''',
    '''        "investigation_goal": (
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
''',
    "beta6 investigation goal",
)

source = replace_once(
    source,
    '''        "maintenance_contexts": maintenance_contexts[:MAX_CONTEXTS],
        "report_contexts": report_contexts[:MAX_CONTEXTS],
        "request_shape_contexts": request_shape_contexts[:MAX_CONTEXTS],
''',
    '''        "maintenance_contexts": maintenance_contexts[:MAX_CONTEXTS],
        "maintenance_ui_contexts": maintenance_ui_contexts[:MAX_CONTEXTS],
        "report_contexts": report_contexts[:MAX_CONTEXTS],
        "report_transport_contexts": report_transport_contexts[:MAX_CONTEXTS],
        "request_shape_contexts": request_shape_contexts[:MAX_CONTEXTS],
        "source_map_fetches": source_map_fetches,
        "source_map_findings": source_map_findings,
''',
    "beta6 output findings",
)

source = replace_once(
    source,
    '"strategy": "semantic_hash_agnostic_priority+targeted_callsite_recovery",',
    '"strategy": "semantic_hash_agnostic_priority+targeted_callsite_recovery+ui_source_map_recovery",',
    "beta6 discovery strategy",
)
source = replace_once(
    source,
    '"failed_request_count": request_count - successful_assets,',
    '"failed_request_count": request_count - successful_assets - source_map_success,',
    "source map request accounting",
)
source = replace_once(
    source,
    '''            "targeted_success_count": targeted_success,
            "unfetched_candidates": unfetched,
''',
    '''            "targeted_success_count": targeted_success,
            "source_map_candidate_count": len(source_map_rows),
            "source_map_fetch_count": len(source_map_fetches),
            "source_map_success_count": source_map_success,
            "unfetched_candidates": unfetched,
''',
    "source map discovery metrics",
)
source = replace_once(
    source,
    '''        "note": (
            "0.4.3-beta5 performs targeted wrapper/call-site recovery on top of the "
            "hash-agnostic crawl, with a reserved high-priority asset pass. It remains "
            "read-only and executes no maintenance mutation or mower command."
        ),
''',
    '''        "note": (
            "0.4.3-beta6 steps back to Parts maintenance UI/i18n/route/source-map "
            "recovery, fixes default-argument handleH5MowerSet wrapper detection, and "
            "keeps Mowing Reports focused on transport proof. It remains read-only and "
            "executes no report API request, maintenance mutation or mower command."
        ),
''',
    "beta6 discovery note",
)
discovery_path.write_text(source, encoding="utf-8")

diagnostics_path = COMPONENT / "diagnostics.py"
diagnostics = diagnostics_path.read_text(encoding="utf-8")
diagnostics = replace_once(
    diagnostics,
    '''    0.4.3-beta5 performs targeted call-site recovery within the bounded read-only public-H5 inspection
    for Maintenance + Mowing Reports contracts; no mutation runs.''',
    '''    0.4.3-beta6 performs Parts maintenance UI/i18n/source-map recovery plus Mowing Reports transport recovery
    within the bounded read-only public-H5 inspection; no mutation or report API request runs.''',
    "diagnostics docstring",
)
diagnostics = replace_once(
    diagnostics,
    '"Normal diagnostics use current coordinator state and caches; 0.4.3-beta5 targets H5 wrapper call sites and report/maintenance payload structure.",',
    '"Normal diagnostics use current coordinator state and caches; 0.4.3-beta6 traces Parts maintenance UI/source maps and the remaining Mowing Reports transport layer.",',
    "diagnostics note",
)
diagnostics_path.write_text(diagnostics, encoding="utf-8")

release_notes_path = ROOT / ".github" / "release-notes" / "0.4.3-beta6.md"
release_notes_path.write_text(
    """title: Navimower 0.4.3-beta6

Navimower 0.4.3-beta6 deliberately broadens the Maintenance investigation one level back to **Parts maintenance UI/i18n/source-map recovery**, while keeping the already recovered Mowing Reports contract focused on its remaining encryption/transport layer.

### Parts maintenance recovery

- Add the actual app UI anchors: `Parts maintenance`, `Blades`, `Used time`, `Remaining time`, `Replacement done`, `Chassis and other parts`, `Clean now`, `Buy now` and `Check now`, plus code-style variants.
- Treat `repair`/parts/chassis/replacement/clean theme evidence as a first-class targeted-crawl signal even when the chunk filename is generic `index-<hash>.js`.
- Increase the reserved targeted pass to 24 successful assets so the full group of repair-themed modern/legacy lazy chunks can be inspected instead of being discarded by filename score.
- Fix `handleH5MowerSet` wrapper recovery for the official default-argument form such as `(e={})=>callNative("handleH5MowerSet",e)` and tighten the regex so it does not span unrelated functions.
- Capture likely maintenance/i18n string keys and dedicated UI contexts.

### Bounded source-map recovery

- Detect public `sourceMappingURL` references from high-value assets and fetch at most 6 public HTTPS source maps, each capped at 4 MiB.
- Parse only bounded metadata and matching original-source contexts; diagnostics never dumps an entire source map or source tree.
- Prioritize maps that contain Parts maintenance UI evidence, `handleH5MowerSet`, repair routes, Mowing Reports endpoints or encryption bridge markers.

### Mowing Reports transport

- Preserve the recovered day/week/month and vehicle-main report contracts and collect dedicated `handleEncipherment` / `handleDecrypt` / transport contexts.
- Record that the observed H5 outer shape (`body.data` after native encipherment) is not assumed to be interchangeable with the private-cloud p:101 `{d,h,k,p,t}` envelope.
- Beta6 therefore executes no live report API request until transport compatibility is proven rather than guessed.

### Safety

Beta6 remains **strictly read-only** and Download-diagnostics-only. It performs bounded public HTTPS GET requests only, sends no account/mower identifiers to H5, executes no report API request, blade reset, Clean now action, maintenance-mode command, cutting-height change or other mower mutation.
""",
    encoding="utf-8",
)

changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
if "## 0.4.3-beta6" in changelog:
    raise SystemExit("0.4.3-beta6 changelog section already exists")
changelog = replace_once(
    changelog,
    "# Changelog\n\n",
    """# Changelog

## 0.4.3-beta6

Parts maintenance UI/source-map recovery and Mowing Reports transport proof.

### Changed

- Broaden Maintenance discovery from guessed endpoint names back to the actual Parts maintenance UI and i18n semantics.
- Target generic repair-themed lazy chunks and increase the reserved targeted asset budget from 16 to 24.
- Recover default-argument `handleH5MowerSet` wrappers such as `(e={})=>...` without spanning unrelated functions.
- Add bounded public source-map discovery for high-value maintenance/report assets.
- Capture likely Parts maintenance translation keys, UI contexts and original-source contexts.
- Keep the recovered Mowing Reports business contract while explicitly comparing H5 `body.data`/native encryption evidence with the existing private-cloud p:101 envelope shape.

### Safety

- Discovery remains Download-diagnostics-only, public, unauthenticated and GET-only.
- No report API request, maintenance counter reset, Clean now action, maintenance mode, cutting-height mutation or mower command is executed.

""",
    "changelog header",
)
changelog_path.write_text(changelog, encoding="utf-8")

test_path = ROOT / "tests" / "test_v043_beta6.py"
test_path.write_text(
    r'''"""Regression contracts for Navimower 0.4.3-beta6 H5 UI/source-map recovery."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _compiled_patterns(source: str) -> dict[str, str]:
    tree = ast.parse(source)
    rows: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        call = node.value
        if not (
            isinstance(target, ast.Name)
            and isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "re"
            and call.func.attr == "compile"
            and call.args
        ):
            continue
        pattern = ast.literal_eval(call.args[0])
        assert isinstance(pattern, str)
        re.compile(pattern)
        rows[target.id] = pattern
    return rows


def test_beta6_version_notes_and_changelog() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta6"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta6.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta6")
    assert "Parts maintenance" in notes
    assert "source-map" in notes
    assert "strictly read-only" in notes
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.startswith("# Changelog\n\n## 0.4.3-beta6")


def test_beta6_uses_real_parts_maintenance_ui_anchors_and_repair_themes() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    for phrase in (
        "MAINTENANCE_UI_TARGETS",
        '"Parts maintenance"',
        '"Replacement done"',
        '"Clean now"',
        '"Chassis and other parts"',
        '"Remaining time"',
        '"Check now"',
        "TARGETED_THEME_TERMS",
        '"repair"',
        "theme_terms = {str(value).lower()",
        "MAX_TARGETED_ASSETS = 24",
    ):
        assert phrase in source


def test_beta6_recovers_default_argument_mower_set_wrapper_without_cross_function_span() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    patterns = _compiled_patterns(source)
    mower_arrow = re.compile(patterns["MOWER_SET_ARROW_WRAPPER_RE"], re.I | re.S)
    match = mower_arrow.search('l5=(e={})=>je.callNative("handleH5MowerSet",e)')
    assert match is not None
    assert match.group("name") == "l5"
    assert match.group("param") == "e"
    mower_function = re.compile(patterns["MOWER_SET_WRAPPER_RE"], re.I | re.S)
    assert mower_function.search('function ab(e){return je.callNative("handleH5MowerSet",e)}')
    false_positive = 'function wrong(e){return e}function right(x){return je.callNative("handleH5MowerSet",x)}'
    match = mower_function.search(false_positive)
    assert match is not None
    assert match.group("name") == "right"


def test_beta6_has_bounded_public_source_map_recovery() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    patterns = _compiled_patterns(source)
    assert "SOURCE_MAP_RE" in patterns
    assert re.compile(patterns["SOURCE_MAP_RE"], re.I).search("//# sourceMappingURL=index.js.map")
    for phrase in (
        "MAX_SOURCE_MAPS = 6",
        "MAX_SOURCE_MAP = 4 * 1024 * 1024",
        "def _source_map_url",
        "def _source_map_priority",
        "def _source_map_findings",
        '"source_map_fetches": source_map_fetches',
        '"source_map_findings": source_map_findings',
        '"source_map_success_count": source_map_success',
    ):
        assert phrase in source


def test_beta6_keeps_reports_transport_only_until_crypto_is_proven() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    for phrase in (
        "REPORT_TRANSPORT_TARGETS",
        '"handleEncipherment"',
        '"handleDecrypt"',
        '"keyDataOne"',
        '"body:{data"',
        '"live_report_request_executed": False',
        '"status": "not_assumed"',
        "p:101 envelope fields d,h,k,p,t",
    ):
        assert phrase in source
    assert "client.call(" not in source


def test_beta6_remains_public_get_only_and_non_mutating() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert 'method="GET"' in source
    assert '"mutation_calls_executed": False' in source
    assert '"public_unauthenticated_h5_only": True' in source
    assert "Authorization" not in source
    assert "Cookie" not in source
    assert "0.4.3-beta6" in diagnostics
    assert "bounded read-only public-H5 inspection" in diagnostics
''',
    encoding="utf-8",
)
