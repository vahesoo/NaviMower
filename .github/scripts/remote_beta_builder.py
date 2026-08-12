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


manifest_path = COMPONENT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != "0.4.3-beta4":
    raise SystemExit(f"Expected 0.4.3-beta4 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta5"
manifest_path.write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

discovery_path = COMPONENT / "maintenance_h5_discovery.py"
source = discovery_path.read_text(encoding="utf-8")
source = replace_once(
    source,
    '"User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.3-beta4",',
    '"User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.3-beta5",',
    "discovery user agent",
)
source = replace_once(
    source,
    "MAX_ASSETS = 48\nMAX_REQUESTS = 96\n",
    "MAX_ASSETS = 48\nMAX_TARGETED_ASSETS = 16\nMAX_REQUESTS = 128\n",
    "targeted reserve constants",
)
source = replace_once(
    source,
    "CONTEXT_RADIUS = 2200\nCANDIDATE_RADIUS = 1500\nTIMEOUT = 5\n",
    "CONTEXT_RADIUS = 2200\nCANDIDATE_RADIUS = 1500\nCALLSITE_RADIUS = 2600\nMAX_CALLSITES_PER_WRAPPER = 8\nTIMEOUT = 5\n",
    "callsite constants",
)

regex_marker = '''BRIDGE_RE = re.compile(
    r"(?P<callee>(?:[A-Za-z_$][\\w$]*\\.)*(?:sendEncryptionData|callNative|sendMessageToNative))"
    r"\\s*\\(\\s*[\\\"'](?P<method>[^\\\"']{1,160})[\\\"']",
    re.I,
)
'''
regex_insert = regex_marker + '''REPORT_WRAPPER_RE = re.compile(
    r"function\\s+(?P<name>[A-Za-z_$][\\w$]*)\\s*\\(\\s*(?P<param>[A-Za-z_$][\\w$]*)\\s*\\)"
    r"\\s*\\{.{0,1400}?[\\\"'](?P<endpoint>/vehicle/report/(?:get-day-week-month-data|vehicle-main-report))[\\\"']",
    re.I | re.S,
)
REPORT_ARROW_WRAPPER_RE = re.compile(
    r"(?P<name>[A-Za-z_$][\\w$]*)\\s*=\\s*(?:async\\s*)?\\(?\\s*(?P<param>[A-Za-z_$][\\w$]*)\\s*\\)?"
    r"\\s*=>.{0,1400}?[\\\"'](?P<endpoint>/vehicle/report/(?:get-day-week-month-data|vehicle-main-report))[\\\"']",
    re.I | re.S,
)
MOWER_SET_WRAPPER_RE = re.compile(
    r"function\\s+(?P<name>[A-Za-z_$][\\w$]*)\\s*\\(\\s*(?P<param>[A-Za-z_$][\\w$]*)\\s*\\)"
    r"\\s*\\{.{0,1800}?(?:callNative|sendMessageToNative)\\s*\\(\\s*[\\\"']handleH5MowerSet[\\\"']",
    re.I | re.S,
)
MOWER_SET_ARROW_WRAPPER_RE = re.compile(
    r"(?P<name>[A-Za-z_$][\\w$]*)\\s*=\\s*(?:async\\s*)?\\(?\\s*(?P<param>[A-Za-z_$][\\w$]*)\\s*\\)?"
    r"\\s*=>.{0,1800}?(?:callNative|sendMessageToNative)\\s*\\(\\s*[\\\"']handleH5MowerSet[\\\"']",
    re.I | re.S,
)
'''
source = replace_once(source, regex_marker, regex_insert, "callsite regex insertion")

helper_marker = "\ndef _filename_bonus(url: str) -> int:\n"
helpers = r'''

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


def _is_targeted_candidate(candidate: dict[str, Any]) -> bool:
    url = str(candidate.get("url") or "")
    return _filename_bonus(url) >= 130 or int(candidate.get("score") or 0) >= 180
'''
source = replace_once(source, helper_marker, helpers + helper_marker, "callsite helper insertion")

vars_marker = '''    bridge_markers: set[tuple[str, str, str]] = set()
    report_endpoints_found: set[str] = set()
    successful_assets = 0
    request_count = 0
'''
vars_insert = '''    bridge_markers: set[tuple[str, str, str]] = set()
    report_endpoints_found: set[str] = set()
    report_wrapper_definitions: list[dict[str, Any]] = []
    report_callsite_contexts: list[dict[str, Any]] = []
    report_field_contexts: list[dict[str, Any]] = []
    mower_set_wrapper_definitions: list[dict[str, Any]] = []
    mower_set_callsite_contexts: list[dict[str, Any]] = []
    targeted_fetches: list[dict[str, Any]] = []
    successful_assets = 0
    request_count = 0
'''
source = replace_once(source, vars_marker, vars_insert, "callsite result variables")

findings_marker = '''        maintenance_contexts.extend(found_maintenance)
        report_contexts.extend(found_reports)
        request_shape_contexts.extend(found_request_shape)
'''
findings_insert = '''        callsite_findings = _callsite_findings(text, url)
        report_wrapper_definitions.extend(callsite_findings["report_wrapper_definitions"])
        report_callsite_contexts.extend(callsite_findings["report_callsite_contexts"])
        report_field_contexts.extend(callsite_findings["report_field_contexts"])
        mower_set_wrapper_definitions.extend(callsite_findings["mower_set_wrapper_definitions"])
        mower_set_callsite_contexts.extend(callsite_findings["mower_set_callsite_contexts"])

        maintenance_contexts.extend(found_maintenance)
        report_contexts.extend(found_reports)
        request_shape_contexts.extend(found_request_shape)
'''
source = replace_once(source, findings_marker, findings_insert, "broad callsite collection")

target_marker = '''        assets.append(row)

    candidate_rows = sorted(
'''
target_pass = r'''        assets.append(row)

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

    candidate_rows = sorted(
'''
source = replace_once(source, target_marker, target_pass, "targeted reserve pass")

return_marker = '''        "bridge_call_contexts": bridge_call_contexts[:MAX_CONTEXTS],
        "request_candidates": request_candidates[:MAX_REQUEST_CANDIDATES],
'''
return_insert = '''        "bridge_call_contexts": bridge_call_contexts[:MAX_CONTEXTS],
        "report_wrapper_definitions": report_wrapper_definitions[:64],
        "report_callsite_contexts": report_callsite_contexts[:96],
        "report_field_contexts": report_field_contexts[:96],
        "mower_set_wrapper_definitions": mower_set_wrapper_definitions[:64],
        "mower_set_callsite_contexts": mower_set_callsite_contexts[:96],
        "targeted_fetches": targeted_fetches,
        "request_candidates": request_candidates[:MAX_REQUEST_CANDIDATES],
'''
source = replace_once(source, return_marker, return_insert, "callsite output fields")
source = replace_once(
    source,
    '"max_successful_assets": MAX_ASSETS,\n            "max_requests": MAX_REQUESTS,',
    '"max_broad_successful_assets": MAX_ASSETS,\n            "max_targeted_successful_assets": MAX_TARGETED_ASSETS,\n            "max_total_successful_assets": MAX_ASSETS + MAX_TARGETED_ASSETS,\n            "max_requests": MAX_REQUESTS,',
    "limits metadata",
)
source = replace_once(
    source,
    '"strategy": "semantic_hash_agnostic_priority",',
    '"strategy": "semantic_hash_agnostic_priority+targeted_callsite_recovery",',
    "discovery strategy",
)
source = replace_once(
    source,
    '"failed_request_count": request_count - successful_assets,\n            "unfetched_candidates": unfetched,',
    '"failed_request_count": request_count - successful_assets,\n            "targeted_fetch_count": len(targeted_fetches),\n            "targeted_success_count": targeted_success,\n            "unfetched_candidates": unfetched,',
    "targeted discovery metrics",
)
source = replace_once(
    source,
    '''        "note": (
            "0.4.3-beta4 is a bounded read-only contract-recovery pass. Hashed chunk "
            "suffixes are treated as build artifacts; semantic chunk prefixes and "
            "import relationships drive priority. No maintenance mutation or mower "
            "command is executed."
        ),''',
    '''        "note": (
            "0.4.3-beta5 performs targeted wrapper/call-site recovery on top of the "
            "hash-agnostic crawl, with a reserved high-priority asset pass. It remains "
            "read-only and executes no maintenance mutation or mower command."
        ),''',
    "beta5 discovery note",
)
discovery_path.write_text(source, encoding="utf-8")

diagnostics_path = COMPONENT / "diagnostics.py"
diagnostics = diagnostics_path.read_text(encoding="utf-8")
diagnostics = replace_once(
    diagnostics,
    '''    0.4.3-beta4 performs broadened bounded read-only public-H5 inspection for
    Maintenance + Mowing Reports contracts and request structure; no mutation runs.''',
    '''    0.4.3-beta5 performs targeted call-site recovery within the bounded read-only public-H5 inspection
    for Maintenance + Mowing Reports contracts; no mutation runs.''',
    "diagnostics docstring",
)
diagnostics = replace_once(
    diagnostics,
    '"Normal diagnostics use current coordinator state and caches; 0.4.3-beta4 recovers bounded public H5 Maintenance + Mowing Reports contracts.",',
    '"Normal diagnostics use current coordinator state and caches; 0.4.3-beta5 targets H5 wrapper call sites and report/maintenance payload structure.",',
    "diagnostics note",
)
diagnostics_path.write_text(diagnostics, encoding="utf-8")

release_notes_path = ROOT / ".github" / "release-notes" / "0.4.3-beta5.md"
release_notes_path.write_text(
    """title: Navimower 0.4.3-beta5

Navimower 0.4.3-beta5 narrows the temporary H5 investigation to **targeted call-site recovery** for Maintenance + Mowing Reports.

### Mowing Reports call-site recovery

- Detect wrapper functions around `/vehicle/report/get-day-week-month-data` and `/vehicle/report/vehicle-main-report`, including minified function and arrow-function forms.
- Find calls to those recovered wrappers in the same asset and preserve their bounded argument previews, nearby object keys, request structure and report-field terms.
- Capture dedicated contexts for `mowingArea`, `mowingTime`, `mowingCount`, `totalMowingArea` and `totalMowingTime` so request inputs and parsed response fields can be correlated instead of guessed.

### Maintenance call-site recovery

- Detect wrapper definitions around the native `handleH5MowerSet` bridge and capture bounded call sites with nearby maintenance/blade/knife/duration terms.
- Continue preserving `handleEncipherment`, `handleDecrypt`, request-wrapper and endpoint context without executing any mower mutation.

### Reserved targeted asset pass

- Keep the existing 48-asset broad crawl, then reserve up to 16 additional successful JavaScript fetches for late-discovered high-priority `maintenance`, `blade`, `knife`, `report`, `request-*`, `native-*`, `service-*`, mower and setting chunks.
- The targeted pass follows high-priority child imports recursively within its own bounded budget, so a valuable chunk discovered near the end of the broad crawl is no longer automatically lost.

### Safety

Beta5 remains **strictly read-only** and Download-diagnostics-only. It performs only bounded public HTTPS GET requests, sends no mower/account identifiers to H5, and executes no blade reset, maintenance-mode command, cutting-height change, report API request or other mower mutation.
""",
    encoding="utf-8",
)

changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
if "## 0.4.3-beta5" in changelog:
    raise SystemExit("0.4.3-beta5 changelog section already exists")
changelog = replace_once(
    changelog,
    "# Changelog\n\n",
    """# Changelog

## 0.4.3-beta5

Targeted Maintenance + Mowing Reports H5 call-site recovery.

### Changed

- Recover minified report wrapper definitions and their call sites for the day/week/month and vehicle-main report endpoints.
- Capture bounded report wrapper arguments and nearby mowing area/time/count response-field contexts.
- Recover `handleH5MowerSet` wrapper definitions and bounded maintenance-related call sites.
- Reserve up to 16 additional successful asset fetches for high-priority chunks discovered after the 48-asset broad crawl.

### Safety

- Discovery remains Download-diagnostics-only, public, unauthenticated and GET-only.
- No report API request, maintenance counter reset, maintenance mode, cutting-height mutation or mower command is executed.

""",
    "changelog header",
)
changelog_path.write_text(changelog, encoding="utf-8")

test_path = ROOT / "tests" / "test_v043_beta5.py"
test_path.write_text(
    r'''"""Regression contracts for Navimower 0.4.3-beta5 targeted H5 call-site recovery."""
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


def test_beta5_version_notes_and_changelog() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta5"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta5.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta5")
    assert "targeted call-site recovery" in notes
    assert "16 additional successful JavaScript fetches" in notes
    assert "strictly read-only" in notes
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.startswith("# Changelog\n\n## 0.4.3-beta5")


def test_beta5_recovers_report_wrappers_and_callsite_fields() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    patterns = _compiled_patterns(source)
    assert "REPORT_WRAPPER_RE" in patterns
    report_re = re.compile(patterns["REPORT_WRAPPER_RE"], re.I | re.S)
    sample = 'function uj(r){return fC("/vehicle/report/vehicle-main-report",{body:{data:r}})}'
    match = report_re.search(sample)
    assert match is not None
    assert match.group("name") == "uj"
    assert match.group("param") == "r"
    assert match.group("endpoint") == "/vehicle/report/vehicle-main-report"
    for phrase in (
        "def _balanced_argument",
        "def _named_callsite_contexts",
        '"report_wrapper_definitions": report_wrapper_definitions',
        '"report_callsite_contexts": report_callsite_contexts',
        '"report_field_contexts": report_field_contexts',
        "argument_preview",
        "mowingArea",
        "mowingTime",
        "totalMowingArea",
        "totalMowingTime",
    ):
        assert phrase in source


def test_beta5_targets_mower_set_bridge_callsites() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    patterns = _compiled_patterns(source)
    assert "MOWER_SET_WRAPPER_RE" in patterns
    mower_re = re.compile(patterns["MOWER_SET_WRAPPER_RE"], re.I | re.S)
    sample = 'function ab(e){return je.callNative("handleH5MowerSet",e)}'
    match = mower_re.search(sample)
    assert match is not None
    assert match.group("name") == "ab"
    assert match.group("param") == "e"
    assert '"mower_set_wrapper_definitions": mower_set_wrapper_definitions' in source
    assert '"mower_set_callsite_contexts": mower_set_callsite_contexts' in source
    assert "maintenance_terms_nearby" in source


def test_beta5_has_reserved_targeted_pass() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    for phrase in (
        "MAX_ASSETS = 48",
        "MAX_TARGETED_ASSETS = 16",
        "MAX_REQUESTS = 128",
        "targeted_priority_reserve",
        "def _is_targeted_candidate",
        "targeted_queue",
        '"targeted_fetches": targeted_fetches',
        '"targeted_fetch_count": len(targeted_fetches)',
        '"targeted_success_count": targeted_success',
        "semantic_hash_agnostic_priority+targeted_callsite_recovery",
    ):
        assert phrase in source


def test_beta5_remains_public_get_only_and_non_mutating() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert 'method="GET"' in source
    assert '"mutation_calls_executed": False' in source
    assert '"public_unauthenticated_h5_only": True' in source
    assert "client.call(" not in source
    assert "Authorization" not in source
    assert "Cookie" not in source
    assert "0.4.3-beta5" in diagnostics
    assert "targeted" in diagnostics.lower()
''',
    encoding="utf-8",
)
