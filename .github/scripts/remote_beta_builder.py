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
if manifest.get("version") != "0.4.3-beta7":
    raise SystemExit(f"Expected 0.4.3-beta7 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta8"
manifest_path.write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)


discovery_path = COMPONENT / "maintenance_h5_discovery.py"
source = discovery_path.read_text(encoding="utf-8")
source = replace_once(
    source,
    '"User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.3-beta7",',
    '"User-Agent": "Mozilla/5.0 NavimowerDiagnostics/0.4.3-beta8",',
    "discovery user agent",
)

source = replace_once(
    source,
    '''TARGETED_THEME_TERMS = (
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
    '''TARGETED_THEME_TERMS = (
    "maintenance",
    "parts",
    "blade",
    "knife",
    "chassis",
    "replacement",
    "clean",
    "report",
    "mowing",
)

# Temporary beta-only fallback for the exact Mowing Records chunk already observed in
# current public H5. Semantic source-context routing remains authoritative and this hint
# is removed with discovery cleanup once the contract is integrated.
OBSERVED_REPORT_ASSET_BASENAMES = (
    "index-594ad42d.js",
)
''',
    "targeted theme routing and report fallback",
)

source = replace_once(
    source,
    "MAX_SOURCE_MAPS = 2\n",
    "MAX_SOURCE_MAPS = 0\n",
    "disable unavailable source maps",
)

source = replace_once(
    source,
    '''MOWER_SET_ARROW_WRAPPER_RE = re.compile(
    r"(?P<name>[A-Za-z_$][\\w$]*)\\s*=\\s*(?:async\\s*)?\\(?\\s*(?P<param>[A-Za-z_$][\\w$]*)"
    r"(?:\\s*=\\s*\\{\\})?\\s*\\)?\\s*=>[^;]{0,700}?"
    r"(?:callNative|sendMessageToNative)\\s*\\(\\s*[\\\"']handleH5MowerSet[\\\"']",
    re.I | re.S,
)
''',
    '''MOWER_SET_ARROW_WRAPPER_RE = re.compile(
    r"(?P<name>[A-Za-z_$][\\w$]*)\\s*=\\s*(?:async\\s*)?\\(?\\s*(?P<param>[A-Za-z_$][\\w$]*)"
    r"(?:\\s*=\\s*\\{\\})?\\s*\\)?\\s*=>\\s*"
    r"(?:(?:[A-Za-z_$][\\w$]*)\\.)*(?:callNative|sendMessageToNative)"
    r"\\s*\\(\\s*[\\\"']handleH5MowerSet[\\\"']",
    re.I,
)
''',
    "precise mower-set arrow wrapper",
)

source = replace_once(
    source,
    '''def _is_targeted_candidate(candidate: dict[str, Any]) -> bool:
    url = str(candidate.get("url") or "")
    theme_terms = {str(value).lower() for value in candidate.get("theme_terms") or []}
    return (
        _filename_bonus(url) >= 130
        or int(candidate.get("score") or 0) >= 180
        or any(term in theme_terms for term in TARGETED_THEME_TERMS)
    )
''',
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


def _is_targeted_candidate(candidate: dict[str, Any]) -> bool:
    return bool(_targeted_reasons(candidate))
''',
    "targeted reason classifier",
)

source = replace_once(
    source,
    '("repair", 310),\n',
    '("repair", 60),\n',
    "deprioritize generic repair filename",
)

source = replace_once(
    source,
    '''    if "skipencryption" in nearby or "needrawresponse" in nearby:
        score += 80
    return score, terms
''',
    '''    if "skipencryption" in nearby or "needrawresponse" in nearby:
        score += 80
    basename = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1].lower()
    if basename in OBSERVED_REPORT_ASSET_BASENAMES:
        score += 900
    return score, terms
''',
    "observed report asset score",
)

source = replace_once(
    source,
    '''        score, terms = _candidate_score(text, match, url)
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
''',
    '''        score, terms = _candidate_score(text, match, url)
        context_lo = max(0, match.start() - 900)
        context_hi = min(len(text), match.end() + 900)
        candidate = {
            "url": url,
            "source": _safe_url(base_url),
            "basename": parsed.path.rsplit("/", 1)[-1],
            "score": score,
            "theme_terms": terms,
            "source_context": re.sub(r"\\s+", " ", text[context_lo:context_hi]).strip(),
            "order": order,
        }
        candidate["targeted_reason"] = _targeted_reasons(candidate)
        rows.append(candidate)
''',
    "candidate source context and targeted reason",
)

source = replace_once(
    source,
    '''    js_candidates: dict[str, dict[str, Any]] = {}
    fetched: set[str] = set()
''',
    '''    js_candidates: dict[str, dict[str, Any]] = {}
    targeted_candidates: dict[str, dict[str, Any]] = {}
    fetched: set[str] = set()
''',
    "targeted candidate registry",
)

source = replace_once(
    source,
    '''        neg_score, _, url, reason = heapq.heappop(queue)
        if url in fetched:
            continue
''',
    '''        neg_score, _, url, reason = heapq.heappop(queue)
        if reason != "root_script" and url in targeted_candidates:
            continue
        if url in fetched:
            continue
''',
    "reserve targeted candidates from broad fetch",
)

source = replace_once(
    source,
    '''        for candidate in discovered:
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
''',
    '''        for candidate in discovered:
            candidate_url = str(candidate["url"])
            previous = js_candidates.get(candidate_url)
            if previous is None or int(candidate["score"]) > int(previous["score"]):
                js_candidates[candidate_url] = candidate
            selected = js_candidates[candidate_url]
            if candidate_url in fetched:
                continue
            if _is_targeted_candidate(selected):
                targeted_candidates[candidate_url] = selected
                continue
            new_score = int(selected["score"])
            if new_score <= best_scores.get(candidate_url, -1):
                continue
            best_scores[candidate_url] = new_score
            heapq.heappush(
                queue,
                (-new_score, sequence, candidate_url, "bounded_lazy_chunk"),
            )
            sequence += 1
''',
    "classify before broad enqueue",
)

source = replace_once(
    source,
    '''    targeted_queue: list[tuple[int, int, str, dict[str, Any]]] = []
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

    targeted_queue_initial_count = len(targeted_queue)
''',
    '''    targeted_queue: list[tuple[int, int, str, dict[str, Any]]] = []
    targeted_queued: set[str] = set()
    targeted_sequence = 0
    targeted_candidate_count_before_targeted_phase = len(targeted_candidates)
    targeted_enqueued_count = 0
    for candidate in targeted_candidates.values():
        candidate_url = str(candidate["url"])
        if candidate_url in fetched:
            continue
        targeted_queued.add(candidate_url)
        heapq.heappush(
            targeted_queue,
            (-int(candidate["score"]), targeted_sequence, candidate_url, candidate),
        )
        targeted_sequence += 1
        targeted_enqueued_count += 1

    targeted_queue_initial_count = len(targeted_queue)
''',
    "build targeted queue from reserved candidates",
)

source = replace_once(
    source,
    '''            "source": candidate.get("source"),
            "theme_terms": candidate.get("theme_terms") or [],
            "candidate_score": -neg_score,
''',
    '''            "source": candidate.get("source"),
            "theme_terms": candidate.get("theme_terms") or [],
            "targeted_reason": candidate.get("targeted_reason") or [],
            "source_context": candidate.get("source_context") or "",
            "candidate_score": -neg_score,
''',
    "targeted fetch routing evidence",
)

source = replace_once(
    source,
    '''        for child in discovered:
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
''',
    '''        for child in discovered:
            child_url = str(child["url"])
            previous = js_candidates.get(child_url)
            if previous is None or int(child["score"]) > int(previous["score"]):
                js_candidates[child_url] = child
            selected_child = js_candidates[child_url]
            if (
                child_url in fetched
                or child_url in targeted_queued
                or not _is_targeted_candidate(selected_child)
            ):
                continue
            targeted_candidates[child_url] = selected_child
            targeted_queued.add(child_url)
            heapq.heappush(
                targeted_queue,
                (-int(selected_child["score"]), targeted_sequence, child_url, selected_child),
            )
            targeted_sequence += 1
            targeted_enqueued_count += 1
''',
    "targeted child routing",
)

source = replace_once(
    source,
    '''                "The observable envelope shapes differ, so beta7 does not guess that "
                "private-cloud p:101 is interchangeable with the H5 native bridge."
''',
    '''                "The observable envelope shapes differ, so beta8 does not guess that "
                "private-cloud p:101 is interchangeable with the H5 native bridge."
''',
    "report assessment version",
)

source = replace_once(
    source,
    '''            "strategy": "semantic_hash_agnostic_priority+independent_targeted_request_reserve+callsite_recovery",
            "candidate_count": len(candidate_rows),
''',
    '''            "strategy": "semantic_source_context_routing+reserved_targeted_queue+precise_mower_set_wrapper",
            "candidate_count": len(candidate_rows),
''',
    "beta8 strategy",
)

source = replace_once(
    source,
    '''            "targeted_queue_initial_count": targeted_queue_initial_count,
            "targeted_fetch_count": len(targeted_fetches),
''',
    '''            "targeted_candidate_count_before_targeted_phase": targeted_candidate_count_before_targeted_phase,
            "targeted_enqueued_count": targeted_enqueued_count,
            "targeted_queue_initial_count": targeted_queue_initial_count,
            "targeted_fetch_count": len(targeted_fetches),
''',
    "targeted routing diagnostics",
)

source = replace_once(
    source,
    '''            "0.4.3-beta7 fixes the beta6 crawl-budget starvation by reserving "
            "independent request budgets for broad and targeted phases, then traces "
            "Parts maintenance and Mowing Reports call sites before low-value source-map "
            "probing. It remains read-only and executes no report API request, maintenance "
            "mutation or mower command."
''',
    '''            "0.4.3-beta8 fixes targeted candidate routing at discovery time, reserves "
            "report/maintenance chunks before broad fetching, and anchors handleH5MowerSet "
            "wrapper recovery to the actual native call expression. Public source-map "
            "probing is disabled after repeated 404s. It remains read-only and executes no "
            "report API request, maintenance mutation or mower command."
''',
    "beta8 note",
)

discovery_path.write_text(source, encoding="utf-8")


diagnostics_path = COMPONENT / "diagnostics.py"
diagnostics = diagnostics_path.read_text(encoding="utf-8")
diagnostics = replace_once(
    diagnostics,
    "0.4.3-beta7 performs Parts maintenance targeted-callsite recovery plus Mowing Reports transport recovery\n    within the bounded read-only public-H5 inspection; broad and targeted request budgets are independent, and no mutation or report API request runs.",
    "0.4.3-beta8 performs source-context candidate routing plus precise Parts maintenance and Mowing Reports call-site recovery\n    within the bounded read-only public-H5 inspection; targeted candidates are reserved before broad fetching, and no mutation or report API request runs.",
    "diagnostics docstring",
)
diagnostics = replace_once(
    diagnostics,
    '"Normal diagnostics use current coordinator state and caches; 0.4.3-beta7 reserves an independent targeted H5 request phase for Parts maintenance and Mowing Reports contract recovery.",',
    '"Normal diagnostics use current coordinator state and caches; 0.4.3-beta8 routes high-value H5 candidates into the targeted queue at discovery time and records the source context/reason for that routing.",',
    "diagnostics note",
)
diagnostics_path.write_text(diagnostics, encoding="utf-8")


changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
entry = """# Changelog

## 0.4.3-beta8

Targeted candidate routing and precise mower-set wrapper recovery.

### Fixed

- Classify high-value H5 candidates when their import/reference is discovered and reserve them from the broad queue, fixing beta7's zero-sized targeted queue.
- Treat `report` and `mowing` source-context evidence as targeted signals; generic `repair` alone is no longer enough to dominate discovery.
- Preserve a bounded source-context preview and explicit targeted reason for each candidate/fetch so route/import decisions are auditable.
- Add a temporary beta-only fallback for the already observed Mowing Records chunk `index-594ad42d.js`, while keeping semantic source-context routing authoritative.
- Anchor arrow-wrapper detection directly to `callNative(\"handleH5MowerSet\", ...)`, preventing the preceding `handleDecrypt` wrapper from being misidentified as the mower-set wrapper.
- Disable public source-map requests after repeated beta6/beta7 404 results.

### Safety

- Discovery remains Download-diagnostics-only, public, unauthenticated and GET-only.
- No live Mowing Reports request, blade timer reset, Replacement done action, Clean now action, maintenance mode, cutting-height mutation or mower command is executed.

"""
if not changelog.startswith("# Changelog\n\n"):
    raise SystemExit("Unexpected changelog header")
changelog = entry + changelog[len("# Changelog\n\n"):]
changelog_path.write_text(changelog, encoding="utf-8")


release_notes = """title: Navimower 0.4.3-beta8

Navimower 0.4.3-beta8 fixes the candidate-routing flaw exposed by beta7 and tightens `handleH5MowerSet` wrapper recovery.

### Targeted routing

- High-value lazy chunks are classified at discovery time and reserved from the broad queue instead of being reconsidered only after the broad phase.
- `report` and `mowing` source context now qualify for the targeted phase alongside Parts maintenance/blade/knife/chassis/replacement/clean evidence.
- Generic `repair` is deliberately de-prioritized because beta7 showed it mostly leads into unrelated after-sales workflows.
- Candidate diagnostics retain a bounded source-context preview and explicit `targeted_reason`.
- The already observed public-H5 Mowing Records asset `index-594ad42d.js` has a temporary beta-only exact fallback so the recovered report contract cannot be lost behind generic crawl ordering.

### Parts maintenance

- Arrow-wrapper extraction is anchored directly to `callNative(\"handleH5MowerSet\", ...)`, so the observed `l5=(e={})=>...handleH5MowerSet...` wrapper is not confused with the preceding decrypt wrapper.
- Continue tracing Parts maintenance / Blades / Replacement done / Chassis and other parts / Clean now from UI and source context to real call sites without executing them.

### Mowing Reports

- Preserve the recovered report business contract and prioritize the Mowing Records chunk plus report/mowing route context for transport/encryption recovery.
- No live report request is made until H5 transport/encryption compatibility is proven.

### Source maps

- Public source-map probing is disabled in beta8 after repeated 404-only results; the request budget stays focused on JavaScript contract recovery.

### Safety

Beta8 remains **strictly read-only** and Download-diagnostics-only. It performs bounded public HTTPS GET requests only, sends no account/mower identifiers to H5, and executes no report API request, blade reset, Replacement done action, Clean now action, maintenance-mode command, cutting-height change or other mower mutation.
"""
notes_path = ROOT / ".github" / "release-notes" / "0.4.3-beta8.md"
notes_path.write_text(release_notes, encoding="utf-8")


test_path = ROOT / "tests" / "test_v043_beta8.py"
test_path.write_text(
    '''"""Regression contracts for Navimower 0.4.3-beta8 candidate routing."""\nfrom __future__ import annotations\n\nimport ast\nimport json\nfrom pathlib import Path\nimport re\n\nROOT = Path(__file__).resolve().parents[1]\nCOMPONENT = ROOT / "custom_components" / "navimower"\n\n\ndef _compiled_patterns(source: str) -> dict[str, tuple[str, int]]:\n    tree = ast.parse(source)\n    rows: dict[str, tuple[str, int]] = {}\n    for node in ast.walk(tree):\n        if not isinstance(node, ast.Assign) or len(node.targets) != 1:\n            continue\n        target = node.targets[0]\n        call = node.value\n        if not (\n            isinstance(target, ast.Name)\n            and isinstance(call, ast.Call)\n            and isinstance(call.func, ast.Attribute)\n            and isinstance(call.func.value, ast.Name)\n            and call.func.value.id == "re"\n            and call.func.attr == "compile"\n            and call.args\n        ):\n            continue\n        pattern = ast.literal_eval(call.args[0])\n        flags = 0\n        if len(call.args) > 1:\n            flag_node = call.args[1]\n            if isinstance(flag_node, ast.Attribute) and flag_node.attr == "I":\n                flags = re.I\n        assert isinstance(pattern, str)\n        re.compile(pattern, flags)\n        rows[target.id] = (pattern, flags)\n    return rows\n\n\ndef test_beta8_version_notes_and_changelog() -> None:\n    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))\n    assert manifest["version"] == "0.4.3-beta8"\n    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta8.md").read_text(encoding="utf-8")\n    assert notes.startswith("title: Navimower 0.4.3-beta8")\n    assert "candidate-routing" in notes\n    assert "strictly read-only" in notes\n    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")\n    assert changelog.startswith("# Changelog\\n\\n## 0.4.3-beta8")\n\n\ndef test_beta8_reserves_targeted_candidates_before_broad_fetch() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    for phrase in (\n        "targeted_candidates: dict[str, dict[str, Any]] = {}",\n        'if reason != "root_script" and url in targeted_candidates:',\n        "if _is_targeted_candidate(selected):",\n        "targeted_candidates[candidate_url] = selected",\n        "for candidate in targeted_candidates.values():",\n        '\"targeted_candidate_count_before_targeted_phase\":',\n        '\"targeted_enqueued_count\": targeted_enqueued_count',\n        '\"targeted_reason\": candidate.get(\"targeted_reason\") or []',\n        '\"source_context\": candidate.get(\"source_context\") or \"\"',\n    ):\n        assert phrase in source\n\n\ndef test_beta8_targets_report_context_and_deprioritizes_generic_repair() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    block = source.split("TARGETED_THEME_TERMS = (", 1)[1].split(")", 1)[0]\n    assert '\"report\"' in block\n    assert '\"mowing\"' in block\n    assert '\"repair\"' not in block\n    assert '(\"repair\", 60)' in source\n    assert '\"index-594ad42d.js\"' in source\n    assert 'reasons.append(\"observed_report_asset\")' in source\n\n\ndef test_beta8_mower_set_arrow_regex_anchors_to_actual_native_call() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    patterns = _compiled_patterns(source)\n    pattern, flags = patterns["MOWER_SET_ARROW_WRAPPER_RE"]\n    regex = re.compile(pattern, flags)\n    sample = '$H=(e={})=>je.sendEncryptionData(\"handleDecrypt\",e),l5=(e={})=>je.callNative(\"handleH5MowerSet\",e),x=1'\n    matches = list(regex.finditer(sample))\n    assert len(matches) == 1\n    assert matches[0].group("name") == "l5"\n    assert matches[0].group("param") == "e"\n\n\ndef test_beta8_disables_unproductive_source_map_fetches() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    assert "MAX_SOURCE_MAPS = 0" in source\n\n\ndef test_beta8_remains_public_get_only_and_non_mutating() -> None:\n    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")\n    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")\n    assert 'method=\"GET\"' in source\n    assert '\"mutation_calls_executed\": False' in source\n    assert '\"live_report_request_executed\": False' in source\n    assert "client.call(" not in source\n    assert "Authorization" not in source\n    assert "Cookie" not in source\n    assert "0.4.3-beta8" in diagnostics\n    assert "bounded read-only public-H5 inspection" in diagnostics\n''',
    encoding="utf-8",
)
