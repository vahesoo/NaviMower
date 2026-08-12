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


def replace_count(text: str, old: str, new: str, count: int, label: str) -> str:
    found = text.count(old)
    if found != count:
        raise SystemExit(f"{label}: expected {count} markers, found {found}")
    return text.replace(old, new)


manifest_path = COMPONENT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != "0.4.3-beta8":
    raise SystemExit(f"Expected 0.4.3-beta8 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta9"
manifest_path.write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)


discovery_path = COMPONENT / "maintenance_h5_discovery.py"
source = discovery_path.read_text(encoding="utf-8")
source = replace_once(
    source,
    '"User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.3-beta8",',
    '"User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.3-beta9",',
    "discovery user agent",
)

for old, new, label in (
    ("MAX_ASSETS = 48", "MAX_ASSETS = 12", "broad asset budget"),
    ("MAX_TARGETED_ASSETS = 24", "MAX_TARGETED_ASSETS = 16", "targeted asset budget"),
    ("MAX_BROAD_REQUESTS = 104", "MAX_BROAD_REQUESTS = 28", "broad request budget"),
    ("MAX_TARGETED_REQUESTS = 56", "MAX_TARGETED_REQUESTS = 28", "targeted request budget"),
    ("MAX_TOTAL_REQUESTS = 168", "MAX_TOTAL_REQUESTS = 64", "total request budget"),
    ("MAX_CONTEXTS = 112", "MAX_CONTEXTS = 48", "context budget"),
    ("MAX_REQUEST_CANDIDATES = 180", "MAX_REQUEST_CANDIDATES = 56", "request candidate budget"),
    ("MAX_JS_CANDIDATES = 220", "MAX_JS_CANDIDATES = 72", "candidate output budget"),
    ("MAX_UNFETCHED_CANDIDATES = 120", "MAX_UNFETCHED_CANDIDATES = 24", "unfetched output budget"),
    ("CONTEXT_RADIUS = 2200", "CONTEXT_RADIUS = 1500", "context radius"),
    ("CANDIDATE_RADIUS = 1500", "CANDIDATE_RADIUS = 700", "candidate radius"),
    ("CALLSITE_RADIUS = 2600", "CALLSITE_RADIUS = 2200", "callsite radius"),
):
    source = replace_once(source, old, new, label)

source = replace_once(
    source,
    '''MOWER_SET_ARROW_WRAPPER_RE = re.compile(
    r"(?P<name>[A-Za-z_$][\\w$]*)\\s*=\\s*(?:async\\s*)?\\(?\\s*(?P<param>[A-Za-z_$][\\w$]*)"
    r"(?:\\s*=\\s*\\{\\})?\\s*\\)?\\s*=>\\s*"
    r"(?:(?:[A-Za-z_$][\\w$]*)\\.)*(?:callNative|sendMessageToNative)"
    r"\\s*\\(\\s*[\\\"']handleH5MowerSet[\\\"']",
    re.I,
)
''',
    '''MOWER_SET_ARROW_WRAPPER_RE = re.compile(
    r"(?P<name>[A-Za-z_$][\\w$]*)\\s*=\\s*(?:async\\s*)?\\(?\\s*(?P<param>[A-Za-z_$][\\w$]*)"
    r"(?:\\s*=\\s*\\{\\})?\\s*\\)?\\s*=>\\s*"
    r"(?:(?:[A-Za-z_$][\\w$]*)\\.)*(?:callNative|sendMessageToNative)"
    r"\\s*\\(\\s*[\\\"']handleH5MowerSet[\\\"']",
    re.I,
)
REPORT_TRANSPORT_ARROW_RE = re.compile(
    r"(?P<name>[A-Za-z_$][\\w$]*)\\s*=\\s*(?:async\\s*)?\\(?\\s*(?P<param>[A-Za-z_$][\\w$]*)\\s*\\)?"
    r"\\s*=>[^;]{0,1000}?(?:sendEncryptionData)\\s*\\(\\s*[\\\"']"
    r"(?P<method>handleEncipherment|handleDecrypt)[\\\"']",
    re.I | re.S,
)
EXPORT_BLOCK_RE = re.compile(
    r"export\\s*\\{(?P<bindings>[^}]{1,12000})\\}",
    re.I,
)
IMPORT_BLOCK_RE = re.compile(
    r"import\\s*\\{(?P<bindings>[^}]{1,12000})\\}\\s*from\\s*[\\\"'](?P<source>[^\\\"']+)[\\\"']",
    re.I,
)
''',
    "beta9 transport and module alias regexes",
)

source = replace_once(
    source,
    '''def _public(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "_text"}


def _small_json''',
    '''def _public(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "_text"}


def _compact_asset_evidence(row: dict[str, Any]) -> dict[str, Any] | None:
    interesting_methods = {"handleH5MowerSet", "handleEncipherment", "handleDecrypt"}
    bridge_calls = [
        item for item in row.get("bridge_calls") or []
        if str(item.get("method") or "") in interesting_methods
    ]
    endpoint_paths = [
        path for path in row.get("endpoint_paths") or []
        if path in REPORT_ENDPOINTS or "maintenance" in str(path).lower()
    ]
    matched_terms = list(row.get("matched_terms") or [])
    markers = list(row.get("request_shape_markers") or [])
    basename = urllib.parse.urlsplit(str(row.get("url") or "")).path.rsplit("/", 1)[-1].lower()
    if not (
        matched_terms
        or endpoint_paths
        or markers
        or bridge_calls
        or basename in OBSERVED_REPORT_ASSET_BASENAMES
    ):
        return None
    return {
        "url": row.get("url"),
        "http_status": row.get("http_status"),
        "body_length_read": row.get("body_length_read"),
        "body_sha256": row.get("body_sha256"),
        "truncated": row.get("truncated"),
        "discovery_reason": row.get("discovery_reason"),
        "candidate_score": row.get("candidate_score"),
        "matched_terms": matched_terms[:28],
        "endpoint_paths": endpoint_paths[:24],
        "request_shape_markers": markers[:12],
        "bridge_calls": bridge_calls[:12],
        "js_reference_count": row.get("js_reference_count", 0),
    }


def _compact_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": row.get("url"),
        "source": row.get("source"),
        "basename": row.get("basename"),
        "score": row.get("score"),
        "theme_terms": list(row.get("theme_terms") or [])[:16],
        "targeted_reason": list(row.get("targeted_reason") or [])[:12],
        "source_context": str(row.get("source_context") or "")[:700],
    }


def _small_json''',
    "compact discovery evidence helpers",
)

source = replace_once(
    source,
    '''def _targeted_reasons(candidate: dict[str, Any]) -> list[str]:
    url = str(candidate.get("url") or "")
    basename = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1].lower()
    theme_terms = {str(value).lower() for value in candidate.get("theme_terms") or []}
    reasons: list[str] = []
    if basename in OBSERVED_REPORT_ASSET_BASENAMES:
        reasons.append("observed_report_asset")
    for term in TARGETED_THEME_TERMS:
        if term in theme_terms:
            reasons.append(f"theme:{term}")
    filename_bonus = _filename_bonus(url)
    if filename_bonus >= 130:
        reasons.append("semantic_filename")
    if int(candidate.get("score") or 0) >= 180:
        reasons.append("high_context_score")
    return list(dict.fromkeys(reasons))
''',
    '''def _targeted_reasons(candidate: dict[str, Any]) -> list[str]:
    url = str(candidate.get("url") or "")
    basename = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1].lower()
    theme_terms = {str(value).lower() for value in candidate.get("theme_terms") or []}
    source_context = str(candidate.get("source_context") or "").lower()
    reasons: list[str] = []
    observed_report = basename in OBSERVED_REPORT_ASSET_BASENAMES
    direct_theme = any(
        term in theme_terms for term in TARGETED_THEME_TERMS if term != "mowing"
    )
    in_mowing_route = "mowing_records" in source_context or "mowingrecords" in source_context
    filename_bonus = _filename_bonus(url)
    if observed_report:
        reasons.append("observed_report_asset")
    for term in TARGETED_THEME_TERMS:
        if term not in theme_terms:
            continue
        if term == "mowing" and not (
            in_mowing_route and (observed_report or filename_bonus >= 130)
        ):
            continue
        reasons.append(f"theme:{term}")
    if filename_bonus >= 130 and (
        direct_theme
        or in_mowing_route
        or any(endpoint.lower() in source_context for endpoint in REPORT_ENDPOINTS)
        or "handleh5mowerset" in source_context
    ):
        reasons.append("semantic_filename")
    if int(candidate.get("score") or 0) >= 180 and (
        observed_report or direct_theme or "semantic_filename" in reasons
    ):
        reasons.append("high_context_score")
    return list(dict.fromkeys(reasons))
''',
    "tight targeted reasons",
)

source = replace_once(
    source,
    '''        context_lo = max(0, match.start() - 900)
        context_hi = min(len(text), match.end() + 900)
''',
    '''        context_lo = max(0, match.start() - 500)
        context_hi = min(len(text), match.end() + 500)
''',
    "compact candidate source context",
)

source = replace_once(
    source,
    '''def _wrapper_definitions(
''',
    '''def _exported_aliases(text: str, local_name: str) -> list[str]:
    aliases: list[str] = []
    for match in EXPORT_BLOCK_RE.finditer(text):
        for binding in match.group("bindings").split(","):
            parts = re.split(r"\\s+as\\s+", binding.strip(), maxsplit=1, flags=re.I)
            if not parts or parts[0].strip() != local_name:
                continue
            exported = parts[1].strip() if len(parts) > 1 else local_name
            if re.fullmatch(r"[A-Za-z_$][\\w$]*", exported) and exported not in aliases:
                aliases.append(exported)
    return aliases


def _import_aliases_for_source(
    text: str,
    base_url: str,
    source_url: str,
    exported_names: list[str],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    wanted = set(exported_names)
    for match in IMPORT_BLOCK_RE.finditer(text):
        resolved_source = _resolve_js_url(base_url, match.group("source"))
        if _safe_url(resolved_source) != _safe_url(source_url):
            continue
        for binding in match.group("bindings").split(","):
            parts = re.split(r"\\s+as\\s+", binding.strip(), maxsplit=1, flags=re.I)
            if not parts:
                continue
            exported = parts[0].strip()
            if exported not in wanted:
                continue
            local = parts[1].strip() if len(parts) > 1 else exported
            if not re.fullmatch(r"[A-Za-z_$][\\w$]*", local):
                continue
            row = {
                "exported_name": exported,
                "local_name": local,
                "imported_from": _safe_url(source_url),
            }
            if row not in rows:
                rows.append(row)
    return rows


def _wrapper_definitions(
''',
    "module alias helpers",
)

source = replace_once(
    source,
    '''def _callsite_findings(text: str, source: str) -> dict[str, list[dict[str, Any]]]:
''',
    '''def _report_transport_wrapper_definitions(text: str, source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in REPORT_TRANSPORT_ARROW_RE.finditer(text):
        nearby = text[max(0, match.start() - 220):min(len(text), match.end() + 900)]
        row = {
            "focus": "report_transport_wrapper_definition",
            "name": match.group("name"),
            "param": match.group("param"),
            "method": match.group("method"),
            "source": _safe_url(source),
            "definition_offset": match.start(),
            "context": re.sub(r"\\s+", " ", nearby).strip(),
        }
        if not any(
            item["name"] == row["name"]
            and item["method"] == row["method"]
            and item["source"] == row["source"]
            for item in rows
        ):
            rows.append(row)
        if len(rows) >= 16:
            break
    return rows


def _callsite_findings(text: str, source: str) -> dict[str, list[dict[str, Any]]]:
''',
    "report transport wrapper helper",
)

source = replace_once(
    source,
    '''    mower_set_definitions = _wrapper_definitions(
        text,
        source,
        (("function", MOWER_SET_WRAPPER_RE), ("arrow", MOWER_SET_ARROW_WRAPPER_RE)),
        "mower_set_wrapper_definition",
    )
    return {
        "report_wrapper_definitions": report_definitions,
''',
    '''    mower_set_definitions = _wrapper_definitions(
        text,
        source,
        (("function", MOWER_SET_WRAPPER_RE), ("arrow", MOWER_SET_ARROW_WRAPPER_RE)),
        "mower_set_wrapper_definition",
    )
    report_transport_definitions = _report_transport_wrapper_definitions(text, source)
    return {
        "report_transport_wrapper_definitions": report_transport_definitions,
        "report_wrapper_definitions": report_definitions,
''',
    "transport findings in callsite bundle",
)

source = replace_once(
    source,
    '''    js_candidates: dict[str, dict[str, Any]] = {}
    targeted_candidates: dict[str, dict[str, Any]] = {}
    fetched: set[str] = set()
''',
    '''    js_candidates: dict[str, dict[str, Any]] = {}
    targeted_candidates: dict[str, dict[str, Any]] = {}
    asset_texts: dict[str, str] = {}
    fetched: set[str] = set()
''',
    "asset text registry",
)

source = replace_once(
    source,
    '''    report_wrapper_definitions: list[dict[str, Any]] = []
    report_callsite_contexts: list[dict[str, Any]] = []
''',
    '''    report_transport_wrapper_definitions: list[dict[str, Any]] = []
    report_wrapper_definitions: list[dict[str, Any]] = []
    report_callsite_contexts: list[dict[str, Any]] = []
''',
    "transport definition accumulator",
)

source = replace_once(
    source,
    '''    mower_set_wrapper_definitions: list[dict[str, Any]] = []
    mower_set_callsite_contexts: list[dict[str, Any]] = []
''',
    '''    mower_set_wrapper_definitions: list[dict[str, Any]] = []
    mower_set_callsite_contexts: list[dict[str, Any]] = []
    mower_set_export_aliases: list[dict[str, Any]] = []
    mower_set_import_aliases: list[dict[str, Any]] = []
''',
    "mower-set alias accumulators",
)

source = replace_count(
    source,
    '''        structure = _structure(text)
''',
    '''        asset_texts[_safe_url(url)] = text
        structure = _structure(text)
''',
    2,
    "remember fetched asset text",
)

source = replace_count(
    source,
    '''        callsite_findings = _callsite_findings(text, url)
        report_wrapper_definitions.extend(callsite_findings["report_wrapper_definitions"])
''',
    '''        callsite_findings = _callsite_findings(text, url)
        report_transport_wrapper_definitions.extend(
            callsite_findings["report_transport_wrapper_definitions"]
        )
        report_wrapper_definitions.extend(callsite_findings["report_wrapper_definitions"])
''',
    2,
    "collect transport wrapper definitions",
)

source = replace_once(
    source,
    '''    candidate_rows = sorted(
''',
    '''    alias_callsite_markers: set[tuple[str, str, int]] = set()
    for definition in mower_set_wrapper_definitions:
        definition_source = _safe_url(str(definition.get("source") or ""))
        local_name = str(definition.get("name") or "")
        definition_text = asset_texts.get(definition_source, "")
        if not definition_source or not local_name or not definition_text:
            continue
        exported_names = _exported_aliases(definition_text, local_name)
        if not exported_names:
            exported_names = [local_name]
        for exported_name in exported_names:
            export_row = {
                "source": definition_source,
                "local_name": local_name,
                "exported_name": exported_name,
            }
            if export_row not in mower_set_export_aliases:
                mower_set_export_aliases.append(export_row)
        for asset_url, asset_text in asset_texts.items():
            if asset_url == definition_source:
                continue
            imports = _import_aliases_for_source(
                asset_text,
                asset_url,
                definition_source,
                exported_names,
            )
            for import_row in imports:
                full_import_row = {"source": asset_url, **import_row}
                if full_import_row not in mower_set_import_aliases:
                    mower_set_import_aliases.append(full_import_row)
                synthetic_definition = {
                    "name": import_row["local_name"],
                    "endpoint": None,
                    "definition_offset": -10000,
                }
                for callsite in _named_callsite_contexts(
                    asset_text,
                    asset_url,
                    [synthetic_definition],
                    "maintenance_mower_set_import_callsite",
                ):
                    marker = (
                        str(callsite.get("source") or ""),
                        str(callsite.get("wrapper_name") or ""),
                        int(callsite.get("call_offset") or -1),
                    )
                    if marker in alias_callsite_markers:
                        continue
                    alias_callsite_markers.add(marker)
                    callsite["exported_name"] = import_row["exported_name"]
                    callsite["imported_from"] = import_row["imported_from"]
                    mower_set_callsite_contexts.append(callsite)

    candidate_rows = sorted(
''',
    "cross-file mower-set alias tracing",
)

source = replace_once(
    source,
    '''    unfetched = [
        row for row in candidate_rows if str(row["url"]) not in fetched
    ][:MAX_UNFETCHED_CANDIDATES]
''',
    '''    unfetched = [
        _compact_candidate(row)
        for row in candidate_rows
        if str(row["url"]) not in fetched and _is_targeted_candidate(row)
    ][:MAX_UNFETCHED_CANDIDATES]
''',
    "compact unfetched evidence",
)

source = replace_once(
    source,
    '''                "The observable envelope shapes differ, so beta8 does not guess that "
                "private-cloud p:101 is interchangeable with the H5 native bridge."
''',
    '''                "The observable envelope shapes differ, so beta9 does not guess that "
                "private-cloud p:101 is interchangeable with the H5 native bridge."
''',
    "report assessment version",
)

source = replace_once(
    source,
    '''        "assets": assets,
        "contexts": contexts[:MAX_CONTEXTS],
''',
    '''        "asset_evidence": [
            evidence
            for row in assets
            if (evidence := _compact_asset_evidence(row)) is not None
        ],
        "contexts": contexts[:12],
''',
    "compact asset output",
)

source = replace_once(
    source,
    '''        "report_wrapper_definitions": report_wrapper_definitions[:64],
''',
    '''        "report_transport_wrapper_definitions": report_transport_wrapper_definitions[:24],
        "report_wrapper_definitions": report_wrapper_definitions[:48],
''',
    "transport wrapper output",
)

source = replace_once(
    source,
    '''        "mower_set_wrapper_definitions": mower_set_wrapper_definitions[:64],
        "mower_set_callsite_contexts": mower_set_callsite_contexts[:96],
''',
    '''        "mower_set_wrapper_definitions": mower_set_wrapper_definitions[:32],
        "mower_set_export_aliases": mower_set_export_aliases[:24],
        "mower_set_import_aliases": mower_set_import_aliases[:48],
        "mower_set_callsite_contexts": mower_set_callsite_contexts[:64],
''',
    "mower-set alias output",
)

source = replace_once(
    source,
    '''        "request_candidates": request_candidates[:MAX_REQUEST_CANDIDATES],
        "bridge_candidates": bridge_candidates[:96],
''',
    '''        "request_candidates": [
            row for row in request_candidates if row.get("focus") != "supporting"
        ][:MAX_REQUEST_CANDIDATES],
        "bridge_candidates": [
            row for row in bridge_candidates
            if row.get("method") in ("handleH5MowerSet", "handleEncipherment", "handleDecrypt")
        ][:24],
''',
    "filter supporting output noise",
)

source = replace_once(
    source,
    '''            "strategy": "semantic_source_context_routing+reserved_targeted_queue+precise_mower_set_wrapper",
            "candidate_count": len(candidate_rows),
            "candidates": candidate_rows[:MAX_JS_CANDIDATES],
''',
    '''            "strategy": "compact_contract_recovery+reserved_targeted_queue+cross_file_alias_trace",
            "candidate_count": len(candidate_rows),
            "candidates": [
                _compact_candidate(row)
                for row in candidate_rows
                if _is_targeted_candidate(row)
            ][:MAX_JS_CANDIDATES],
''',
    "compact candidate output",
)

source = replace_once(
    source,
    '''            "0.4.3-beta8 fixes targeted candidate routing at discovery time, reserves "
            "report/maintenance chunks before broad fetching, and anchors handleH5MowerSet "
            "wrapper recovery to the actual native call expression. Public source-map "
            "probing is disabled after repeated 404s. It remains read-only and executes no "
            "report API request, maintenance mutation or mower command."
''',
    '''            "0.4.3-beta9 narrows public-H5 fetching and diagnostics output to proven "
            "Mowing Reports transport evidence and Parts maintenance call-site recovery, "
            "including cross-file handleH5MowerSet export/import alias tracing. It remains "
            "read-only and executes no report API request, maintenance mutation or mower command."
''',
    "beta9 note",
)

discovery_path.write_text(source, encoding="utf-8")


diagnostics_path = COMPONENT / "diagnostics.py"
diagnostics = diagnostics_path.read_text(encoding="utf-8")
diagnostics = replace_once(
    diagnostics,
    "0.4.3-beta8 performs source-context candidate routing plus precise Parts maintenance and Mowing Reports call-site recovery\n    within the bounded read-only public-H5 inspection; targeted candidates are reserved before broad fetching, and no mutation or report API request runs.",
    "0.4.3-beta9 performs compact Mowing Reports transport recovery plus cross-file Parts maintenance alias/call-site tracing\n    within the bounded read-only public-H5 inspection; crawler budgets and output are reduced, and no mutation or report API request runs.",
    "diagnostics docstring",
)
diagnostics = replace_once(
    diagnostics,
    '"Normal diagnostics use current coordinator state and caches; 0.4.3-beta8 routes high-value H5 candidates into the targeted queue at discovery time and records the source context/reason for that routing.",',
    '"Normal diagnostics use current coordinator state and caches; 0.4.3-beta9 keeps the H5 probe compact and focused on report transport plus handleH5MowerSet export/import call-site evidence.",',
    "diagnostics note",
)
diagnostics_path.write_text(diagnostics, encoding="utf-8")


changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
entry = """# Changelog

## 0.4.3-beta9

Compact contract recovery for Mowing Reports transport and Parts maintenance call sites.

### Changed

- Reduce broad H5 discovery from 48 to 12 successful assets and targeted discovery from 24 to 16, with a 64-request total ceiling instead of 168.
- Reduce context/candidate output and replace the full per-asset dump with compact evidence rows containing only contract-relevant fields.
- Tighten targeted routing so incidental `mowing` context no longer promotes unrelated neighboring route assets by score alone.
- Keep the already observed Mowing Records chunk fallback and direct request/native dependencies discoverable.

### Added

- Recover dedicated `handleEncipherment` / `handleDecrypt` wrapper definitions as report transport evidence.
- Trace `handleH5MowerSet` across ES-module export/import aliases so a wrapper exported by app-entry can be followed into lazy-chunk callers.
- Report compact mower-set export aliases, import aliases and imported call-site contexts in diagnostics.

### Safety

- Discovery remains Download-diagnostics-only, public, unauthenticated and GET-only.
- No live Mowing Reports request, blade timer reset, Replacement done action, Clean now action, maintenance mode, cutting-height mutation or mower command is executed.

"""
if not changelog.startswith("# Changelog\n\n"):
    raise SystemExit("Unexpected changelog header")
changelog = entry + changelog[len("# Changelog\n\n"):]
changelog_path.write_text(changelog, encoding="utf-8")


release_notes = """title: Navimower 0.4.3-beta9

Navimower 0.4.3-beta9 puts the beta8 public-H5 crawler on a diet and focuses the remaining discovery on the two unresolved contracts: Mowing Reports transport and Parts maintenance call sites.

### Compact discovery

- Broad successful-asset budget is reduced from 48 to 12 and targeted budget from 24 to 16; the total request ceiling drops from 168 to 64.
- The full `assets` dump is replaced by compact `asset_evidence`, and candidate/context output is bounded much more tightly.
- Incidental `mowing` context is no longer enough to promote unrelated neighboring routes; observed Mowing Records plus direct report/request/native/maintenance evidence stays prioritized.

### Mowing Reports

- Keep the proven `/vehicle/report/vehicle-main-report` and `/vehicle/report/get-day-week-month-data` business contract and the observed Mowing Records chunk fallback.
- Recover `handleEncipherment` and `handleDecrypt` wrapper definitions explicitly so the remaining transport boundary is easier to compare with Navimower's private-cloud transport.
- No live report request is made because H5 native-bridge encryption is still not assumed interchangeable with the private-cloud p:101 envelope.

### Parts maintenance

- Keep the proven `l5=(e={})=>...callNative(\"handleH5MowerSet\",e)` wrapper recovery.
- Follow that wrapper through modern ES-module `export{... as ...}` and `import{... as ...}` aliases into fetched lazy chunks, then capture actual imported call sites and their argument previews when present.

### Safety

Beta9 remains **strictly read-only** and Download-diagnostics-only. It performs bounded public HTTPS GET requests only, sends no account/mower identifiers to H5, and executes no report API request, blade reset, Replacement done action, Clean now action, maintenance-mode command, cutting-height change or other mower mutation.
"""
notes_path = ROOT / ".github" / "release-notes" / "0.4.3-beta9.md"
notes_path.write_text(release_notes, encoding="utf-8")


test_path = ROOT / "tests" / "test_v043_beta9.py"
test_path.write_text(
    '''"""Regression contracts for Navimower 0.4.3-beta9 compact contract recovery."""\nfrom __future__ import annotations\n\nimport ast\nimport json\nfrom pathlib import Path\nimport re\n\nROOT = Path(__file__).resolve().parents[1]\nCOMPONENT = ROOT / "custom_components" / "navimower"\n\n\ndef _compiled_patterns(source: str) -> dict[str, tuple[str, int]]:\n    tree = ast.parse(source)\n    rows: dict[str, tuple[str, int]] = {}\n    for node in ast.walk(tree):\n        if not isinstance(node, ast.Assign) or len(node.targets) != 1:\n            continue\n        target = node.targets[0]\n        call = node.value\n        if not (\n            isinstance(target, ast.Name)\n            and isinstance(call, ast.Call)\n            and isinstance(call.func, ast.Attribute)\n            and isinstance(call.func.value, ast.Name)\n            and call.func.value.id == "re"\n            and call.func.attr == "compile"\n            and call.args\n        ):\n            continue\n        pattern = ast.literal_eval(call.args[0])\n        assert isinstance(pattern, str)\n        re.compile(pattern)\n        rows[target.id] = (pattern, 0)\n    return rows\n\n\ndef test_beta9_version_notes_and_changelog() -> None:\n    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))\n    assert manifest["version"] == "0.4.3-beta9"\n    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta9.md").read_text(encoding="utf-8")\n    assert notes.startswith("title: Navimower 0.4.3-beta9")\n    assert "Compact discovery" in notes\n    assert "strictly read-only" in notes\n    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")\n    assert changelog.startswith("# Changelog\\n\\n## 0.4.3-beta9")\n\n\ndef test_beta9_reduces_crawl_and_output_budget() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    for phrase in (\n        "MAX_ASSETS = 12",\n        "MAX_TARGETED_ASSETS = 16",\n        "MAX_TOTAL_REQUESTS = 64",\n        "MAX_CONTEXTS = 48",\n        "MAX_JS_CANDIDATES = 72",\n        '"asset_evidence": [',\n        "def _compact_asset_evidence",\n        "def _compact_candidate",\n    ):\n        assert phrase in source\n    assert '\"assets\": assets' not in source\n\n\ndef test_beta9_tightens_incidental_mowing_routing() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    assert 'term != "mowing"' in source\n    assert '"mowing_records" in source_context' in source\n    assert 'basename in OBSERVED_REPORT_ASSET_BASENAMES' in source\n    assert '"index-594ad42d.js"' in source\n\n\ndef test_beta9_recovers_report_transport_wrappers() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    patterns = _compiled_patterns(source)\n    assert "REPORT_TRANSPORT_ARROW_RE" in patterns\n    regex = re.compile(patterns["REPORT_TRANSPORT_ARROW_RE"][0], re.I | re.S)\n    sample = 'XH=e=>je.sendEncryptionData("handleEncipherment",e).then(a=>a),$H=e=>je.sendEncryptionData("handleDecrypt",e).then(a=>a);'\n    matches = list(regex.finditer(sample))\n    assert [(m.group("name"), m.group("method")) for m in matches] == [\n        ("XH", "handleEncipherment"),\n        ("$H", "handleDecrypt"),\n    ]\n    assert '\"report_transport_wrapper_definitions\"' in source\n\n\ndef test_beta9_has_cross_file_mower_set_alias_trace() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    patterns = _compiled_patterns(source)\n    export_re = re.compile(patterns["EXPORT_BLOCK_RE"][0], re.I)\n    import_re = re.compile(patterns["IMPORT_BLOCK_RE"][0], re.I)\n    export_match = export_re.search('const l5=e=>e;export{l5 as ac,x as y};')\n    assert export_match is not None and "l5 as ac" in export_match.group("bindings")\n    import_match = import_re.search('import{ac as M,q as z}from"./app-entry.js";M({type:1});')\n    assert import_match is not None and "ac as M" in import_match.group("bindings")\n    for phrase in (\n        "def _exported_aliases",\n        "def _import_aliases_for_source",\n        '\"mower_set_export_aliases\": mower_set_export_aliases',\n        '\"mower_set_import_aliases\": mower_set_import_aliases',\n        '"maintenance_mower_set_import_callsite"',\n    ):\n        assert phrase in source\n\n\ndef test_beta9_keeps_precise_mower_set_wrapper() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    patterns = _compiled_patterns(source)\n    regex = re.compile(patterns["MOWER_SET_ARROW_WRAPPER_RE"][0], re.I)\n    sample = '$H=(e={})=>je.sendEncryptionData("handleDecrypt",e),l5=(e={})=>je.callNative("handleH5MowerSet",e),x=1'\n    match = regex.search(sample)\n    assert match is not None\n    assert match.group("name") == "l5"\n    assert match.group("param") == "e"\n\n\ndef test_beta9_remains_public_get_only_and_non_mutating() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")\n    assert 'method=\"GET\"' in source\n    assert '\"mutation_calls_executed\": False' in source\n    assert '\"live_report_request_executed\": False' in source\n    assert "client.call(" not in source\n    assert "Authorization" not in source\n    assert "Cookie" not in source\n    assert "0.4.3-beta9" in diagnostics\n    assert "bounded read-only public-H5 inspection" in diagnostics\n''',
    encoding="utf-8",
)
