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
if manifest.get("version") != "0.4.3-beta6":
    raise SystemExit(f"Expected 0.4.3-beta6 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta7"
manifest_path.write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)


discovery_path = COMPONENT / "maintenance_h5_discovery.py"
source = discovery_path.read_text(encoding="utf-8")
source = replace_once(
    source,
    '"User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.3-beta6",',
    '"User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.3-beta7",',
    "discovery user agent",
)

source = replace_once(
    source,
    '''    "mallEntranceUrl",
    "knife",
    "chassis",
)
''',
    '''    "mallEntranceUrl",
    "knife",
    "chassis",
    "Time to clean your mower",
    "Maintenance point reached",
    "review parts usage",
    "start cleaning",
    "reset the timer",
    "partsUsage",
    "maintenancePoint",
    "cleanMower",
)
''',
    "beta7 maintenance UI evidence",
)

source = replace_once(
    source,
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
    '''MAX_ASSETS = 48
MAX_TARGETED_ASSETS = 24
MAX_BROAD_REQUESTS = 104
MAX_TARGETED_REQUESTS = 56
MAX_TOTAL_REQUESTS = 168
# Compatibility alias for historical diagnostics/tests; phase limits above are authoritative.
MAX_REQUESTS = MAX_TOTAL_REQUESTS
MAX_CONTEXTS = 112
MAX_REQUEST_CANDIDATES = 180
MAX_JS_CANDIDATES = 220
MAX_UNFETCHED_CANDIDATES = 120
SMALL_JSON_MAX = 8192
MAX_SOURCE_MAPS = 2
MAX_SOURCE_MAP = 4 * 1024 * 1024
MAX_SOURCE_MAP_MATCHING_SOURCES = 32
''',
    "independent request budgets",
)

source = replace_once(
    source,
    '''    successful_assets = 0
    request_count = 0

    while queue and successful_assets < MAX_ASSETS and request_count < MAX_REQUESTS:
''',
    '''    successful_assets = 0
    request_count = 0
    broad_request_count = 0
    targeted_request_count = 0
    source_map_request_count = 0

    while (
        queue
        and successful_assets < MAX_ASSETS
        and broad_request_count < MAX_BROAD_REQUESTS
        and request_count < MAX_TOTAL_REQUESTS
    ):
''',
    "broad request budget",
)

source = replace_once(
    source,
    '''        fetched.add(url)
        request_count += 1
        result = _fetch(url, MAX_JS)
        text = str(result.get("_text") or "")
        row = _public(result)
        row["kind"] = "asset"
        row["discovery_reason"] = reason
''',
    '''        fetched.add(url)
        request_count += 1
        broad_request_count += 1
        result = _fetch(url, MAX_JS)
        text = str(result.get("_text") or "")
        row = _public(result)
        row["kind"] = "asset"
        row["discovery_reason"] = reason
''',
    "broad request counter",
)

source = replace_once(
    source,
    '''    targeted_success = 0
    while (
        targeted_queue
        and targeted_success < MAX_TARGETED_ASSETS
        and request_count < MAX_REQUESTS
    ):
''',
    '''    targeted_queue_initial_count = len(targeted_queue)
    targeted_success = 0
    while (
        targeted_queue
        and targeted_success < MAX_TARGETED_ASSETS
        and targeted_request_count < MAX_TARGETED_REQUESTS
        and request_count < MAX_TOTAL_REQUESTS
    ):
''',
    "targeted request budget",
)

source = replace_once(
    source,
    '''        fetched.add(url)
        request_count += 1
        result = _fetch(url, MAX_JS)
        text = str(result.get("_text") or "")
        row = _public(result)
        row["kind"] = "asset"
        row["discovery_reason"] = "targeted_priority_reserve"
''',
    '''        fetched.add(url)
        request_count += 1
        targeted_request_count += 1
        result = _fetch(url, MAX_JS)
        text = str(result.get("_text") or "")
        row = _public(result)
        row["kind"] = "asset"
        row["discovery_reason"] = "targeted_priority_reserve"
''',
    "targeted request counter",
)

source = replace_once(
    source,
    '''        targeted_record: dict[str, Any] = {
            "url": _safe_url(url),
            "basename": candidate.get("basename"),
            "candidate_score": -neg_score,
            "ok": bool(result.get("ok")),
            "http_status": result.get("http_status"),
        }
''',
    '''        targeted_record: dict[str, Any] = {
            "url": _safe_url(url),
            "basename": candidate.get("basename"),
            "source": candidate.get("source"),
            "theme_terms": candidate.get("theme_terms") or [],
            "candidate_score": -neg_score,
            "ok": bool(result.get("ok")),
            "http_status": result.get("http_status"),
        }
''',
    "targeted diagnostics detail",
)

source = replace_once(
    source,
    '''    for map_candidate in source_map_rows[:MAX_SOURCE_MAPS]:
        if request_count >= MAX_REQUESTS:
            break
        map_url = str(map_candidate["url"])
        request_count += 1
        result = _fetch(map_url, MAX_SOURCE_MAP)
''',
    '''    for map_candidate in source_map_rows[:MAX_SOURCE_MAPS]:
        if request_count >= MAX_TOTAL_REQUESTS:
            break
        map_url = str(map_candidate["url"])
        request_count += 1
        source_map_request_count += 1
        result = _fetch(map_url, MAX_SOURCE_MAP)
''',
    "source map budget after targeted pass",
)

source = replace_once(
    source,
    '''            "max_total_successful_assets": MAX_ASSETS + MAX_TARGETED_ASSETS,
            "max_requests": MAX_REQUESTS,
            "max_contexts": MAX_CONTEXTS,
''',
    '''            "max_total_successful_assets": MAX_ASSETS + MAX_TARGETED_ASSETS,
            "max_broad_requests": MAX_BROAD_REQUESTS,
            "max_targeted_requests": MAX_TARGETED_REQUESTS,
            "max_total_requests": MAX_TOTAL_REQUESTS,
            "max_requests": MAX_REQUESTS,
            "max_contexts": MAX_CONTEXTS,
''',
    "phase request limits output",
)

source = replace_once(
    source,
    '''                "The observable envelope shapes differ, so beta6 does not guess that "
                "private-cloud p:101 is interchangeable with the H5 native bridge."
''',
    '''                "The observable envelope shapes differ, so beta7 does not guess that "
                "private-cloud p:101 is interchangeable with the H5 native bridge."
''',
    "report assessment version",
)

source = replace_once(
    source,
    '''            "strategy": "semantic_hash_agnostic_priority+targeted_callsite_recovery+ui_source_map_recovery",
            "candidate_count": len(candidate_rows),
            "candidates": candidate_rows[:MAX_JS_CANDIDATES],
            "fetched_count": len(fetched),
            "successful_asset_count": successful_assets,
            "request_count": request_count,
            "failed_request_count": request_count - successful_assets - source_map_success,
            "targeted_fetch_count": len(targeted_fetches),
            "targeted_success_count": targeted_success,
''',
    '''            "strategy": "semantic_hash_agnostic_priority+independent_targeted_request_reserve+callsite_recovery",
            "candidate_count": len(candidate_rows),
            "candidates": candidate_rows[:MAX_JS_CANDIDATES],
            "fetched_count": len(fetched),
            "successful_asset_count": successful_assets,
            "request_count": request_count,
            "broad_request_count": broad_request_count,
            "targeted_request_count": targeted_request_count,
            "source_map_request_count": source_map_request_count,
            "failed_request_count": request_count - successful_assets - source_map_success,
            "targeted_queue_initial_count": targeted_queue_initial_count,
            "targeted_fetch_count": len(targeted_fetches),
            "targeted_success_count": targeted_success,
            "targeted_request_reserve_exhausted": targeted_request_count >= MAX_TARGETED_REQUESTS,
''',
    "beta7 request accounting",
)

source = replace_once(
    source,
    '''            "max_source_maps": MAX_SOURCE_MAPS,
''',
    '''            "max_source_maps": MAX_SOURCE_MAPS,
''',
    "source map limit marker",
)

source = replace_once(
    source,
    '''            "0.4.3-beta6 steps back to Parts maintenance UI/i18n/route/source-map "
            "recovery, fixes default-argument handleH5MowerSet wrapper detection, and "
            "keeps Mowing Reports focused on transport proof. It remains read-only and "
            "executes no report API request, maintenance mutation or mower command."
''',
    '''            "0.4.3-beta7 fixes the beta6 crawl-budget starvation by reserving "
            "independent request budgets for broad and targeted phases, then traces "
            "Parts maintenance and Mowing Reports call sites before low-value source-map "
            "probing. It remains read-only and executes no report API request, maintenance "
            "mutation or mower command."
''',
    "beta7 note",
)

discovery_path.write_text(source, encoding="utf-8")


diagnostics_path = COMPONENT / "diagnostics.py"
diagnostics = diagnostics_path.read_text(encoding="utf-8")
diagnostics = replace_once(
    diagnostics,
    "0.4.3-beta6 performs Parts maintenance UI/i18n/source-map recovery plus Mowing Reports transport recovery\n    within the bounded read-only public-H5 inspection; no mutation or report API request runs.",
    "0.4.3-beta7 performs Parts maintenance targeted-callsite recovery plus Mowing Reports transport recovery\n    within the bounded read-only public-H5 inspection; broad and targeted request budgets are independent, and no mutation or report API request runs.",
    "diagnostics docstring",
)
diagnostics = replace_once(
    diagnostics,
    '"Normal diagnostics use current coordinator state and caches; 0.4.3-beta6 traces Parts maintenance UI/source maps and the remaining Mowing Reports transport layer.",',
    '"Normal diagnostics use current coordinator state and caches; 0.4.3-beta7 reserves an independent targeted H5 request phase for Parts maintenance and Mowing Reports contract recovery.",',
    "diagnostics note",
)
diagnostics_path.write_text(diagnostics, encoding="utf-8")


changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
entry = """# Changelog

## 0.4.3-beta7

Independent targeted-request reserve for Parts maintenance and Mowing Reports discovery.

### Fixed

- Fix beta6 crawl-budget starvation: broad crawling can no longer consume the request budget reserved for the targeted phase.
- Give broad and targeted phases separate bounded request ceilings while retaining an overall request ceiling.
- Expose broad/targeted/source-map request counts and the targeted queue size in diagnostics so the reserve can be verified directly.
- Keep the 24-success targeted asset goal and include candidate source/theme evidence in targeted fetch diagnostics.
- De-prioritize public source-map probing after beta6 showed the sampled `.map` URLs were unavailable; targeted JS contract recovery now runs first.
- Add maintenance notification/UI evidence such as `Time to clean your mower`, `Maintenance point reached`, `review parts usage`, `start cleaning` and `reset the timer` to the search vocabulary.

### Safety

- Discovery remains Download-diagnostics-only, public, unauthenticated and GET-only.
- No live Mowing Reports request, blade timer reset, Replacement done action, Clean now action, maintenance mode, cutting-height mutation or mower command is executed.

"""
if not changelog.startswith("# Changelog\n\n"):
    raise SystemExit("Unexpected changelog header")
changelog = entry + changelog[len("# Changelog\n\n"):]
changelog_path.write_text(changelog, encoding="utf-8")


release_notes = """title: Navimower 0.4.3-beta7

Navimower 0.4.3-beta7 fixes the beta6 discovery-budget starvation and guarantees a real **targeted Parts maintenance + Mowing Reports pass** after the broad crawl.

### Targeted request reserve

- Broad and targeted asset phases now have independent bounded request ceilings instead of competing for one shared 160-request budget.
- Broad discovery may use at most 104 asset requests; the targeted phase has its own reserve of up to 56 requests to obtain up to 24 successful high-value assets.
- An overall 168-request ceiling remains in place, preserving bounded diagnostics behavior.
- Diagnostics records the initial targeted queue size plus broad, targeted and source-map request counts, so it is immediately visible whether the reserve actually executed.
- Targeted fetch rows now retain the candidate source and theme terms that caused the chunk to be selected.

### Parts maintenance

- Keep UI/route/theme tracing for Parts maintenance, Blades, Replacement done, Chassis and other parts and Clean now.
- Add observed maintenance-notification wording such as `Time to clean your mower`, `Maintenance point reached`, `review parts usage`, `start cleaning` and `reset the timer` as additional correlation anchors.
- Continue recovering `handleH5MowerSet` definitions and real call sites without executing them.

### Mowing Reports

- Preserve the recovered day/week/month and vehicle-main report business contracts and continue tracing `handleEncipherment` / `handleDecrypt` transport evidence.
- No live report request is made until the H5 transport/encryption compatibility is proven.

### Source maps

- Beta6 found the sampled public `.js.map` URLs unavailable. Beta7 therefore runs targeted JavaScript recovery first and limits source-map probing to two high-value candidates.

### Safety

Beta7 remains **strictly read-only** and Download-diagnostics-only. It performs bounded public HTTPS GET requests only, sends no account/mower identifiers to H5, and executes no report API request, blade reset, Replacement done action, Clean now action, maintenance-mode command, cutting-height change or other mower mutation.
"""
notes_path = ROOT / ".github" / "release-notes" / "0.4.3-beta7.md"
notes_path.write_text(release_notes, encoding="utf-8")


test_path = ROOT / "tests" / "test_v043_beta7.py"
test_path.write_text(
    '''"""Regression contracts for Navimower 0.4.3-beta7 targeted request reserve."""\nfrom __future__ import annotations\n\nimport json\nfrom pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\nCOMPONENT = ROOT / "custom_components" / "navimower"\n\n\ndef test_beta7_version_notes_and_changelog() -> None:\n    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))\n    assert manifest["version"] == "0.4.3-beta7"\n    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta7.md").read_text(encoding="utf-8")\n    assert notes.startswith("title: Navimower 0.4.3-beta7")\n    assert "targeted" in notes.lower()\n    assert "strictly read-only" in notes\n    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")\n    assert changelog.startswith("# Changelog\\n\\n## 0.4.3-beta7")\n\n\ndef test_beta7_separates_broad_and_targeted_request_budgets() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    for phrase in (\n        "MAX_BROAD_REQUESTS = 104",\n        "MAX_TARGETED_REQUESTS = 56",\n        "MAX_TOTAL_REQUESTS = 168",\n        "broad_request_count < MAX_BROAD_REQUESTS",\n        "targeted_request_count < MAX_TARGETED_REQUESTS",\n        "request_count < MAX_TOTAL_REQUESTS",\n        "broad_request_count += 1",\n        "targeted_request_count += 1",\n    ):\n        assert phrase in source\n\n\ndef test_beta7_targeted_phase_is_observable_and_runs_before_source_maps() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    for phrase in (\n        "targeted_queue_initial_count = len(targeted_queue)",\n        '\"broad_request_count\": broad_request_count',\n        '\"targeted_request_count\": targeted_request_count',\n        '\"targeted_queue_initial_count\": targeted_queue_initial_count',\n        '\"targeted_request_reserve_exhausted\":',\n        '\"source\": candidate.get(\"source\")',\n        '\"theme_terms\": candidate.get(\"theme_terms\") or []',\n    ):\n        assert phrase in source\n    assert source.index("targeted_queue_initial_count = len(targeted_queue)") < source.index("source_map_success = 0")\n\n\ndef test_beta7_deprioritizes_source_maps_after_beta6_404s() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    assert "MAX_SOURCE_MAPS = 2" in source\n    assert "source_map_request_count += 1" in source\n    assert source.index("while (\\n        targeted_queue") < source.index("for map_candidate in source_map_rows[:MAX_SOURCE_MAPS]")\n\n\ndef test_beta7_adds_observed_parts_maintenance_correlation_anchors() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    for phrase in (\n        '\"Time to clean your mower\"',\n        '\"Maintenance point reached\"',\n        '\"review parts usage\"',\n        '\"start cleaning\"',\n        '\"reset the timer\"',\n        '\"handleH5MowerSet\"',\n    ):\n        assert phrase in source\n\n\ndef test_beta7_remains_public_get_only_and_non_mutating() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")\n    assert 'method="GET"' in source\n    assert '\"mutation_calls_executed\": False' in source\n    assert '\"live_report_request_executed\": False' in source\n    assert "client.call(" not in source\n    assert "Authorization" not in source\n    assert "Cookie" not in source\n    assert "0.4.3-beta7" in diagnostics\n    assert "bounded read-only public-H5 inspection" in diagnostics\n''',
    encoding="utf-8",
)
