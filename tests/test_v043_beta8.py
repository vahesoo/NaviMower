"""Regression contracts for Navimower 0.4.3-beta8 candidate routing."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _compiled_patterns(source: str) -> dict[str, tuple[str, int]]:
    tree = ast.parse(source)
    rows: dict[str, tuple[str, int]] = {}
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
        flags = 0
        if len(call.args) > 1:
            flag_node = call.args[1]
            if isinstance(flag_node, ast.Attribute) and flag_node.attr == "I":
                flags = re.I
        assert isinstance(pattern, str)
        re.compile(pattern, flags)
        rows[target.id] = (pattern, flags)
    return rows


def test_beta8_version_notes_and_changelog() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta8"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta8.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta8")
    assert "candidate-routing" in notes
    assert "strictly read-only" in notes
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.startswith("# Changelog\n\n## 0.4.3-beta8")


def test_beta8_reserves_targeted_candidates_before_broad_fetch() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    for phrase in (
        "targeted_candidates: dict[str, dict[str, Any]] = {}",
        'if reason != "root_script" and url in targeted_candidates:',
        "if _is_targeted_candidate(selected):",
        "targeted_candidates[candidate_url] = selected",
        "for candidate in targeted_candidates.values():",
        '"targeted_candidate_count_before_targeted_phase":',
        '"targeted_enqueued_count": targeted_enqueued_count',
        '"targeted_reason": candidate.get("targeted_reason") or []',
        '"source_context": candidate.get("source_context") or ""',
    ):
        assert phrase in source


def test_beta8_targets_report_context_and_deprioritizes_generic_repair() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    block = source.split("TARGETED_THEME_TERMS = (", 1)[1].split(")", 1)[0]
    assert '"report"' in block
    assert '"mowing"' in block
    assert '"repair"' not in block
    assert '("repair", 60)' in source
    assert '"index-594ad42d.js"' in source
    assert 'reasons.append("observed_report_asset")' in source


def test_beta8_mower_set_arrow_regex_anchors_to_actual_native_call() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    patterns = _compiled_patterns(source)
    pattern, flags = patterns["MOWER_SET_ARROW_WRAPPER_RE"]
    regex = re.compile(pattern, flags)
    sample = '$H=(e={})=>je.sendEncryptionData("handleDecrypt",e),l5=(e={})=>je.callNative("handleH5MowerSet",e),x=1'
    matches = list(regex.finditer(sample))
    assert len(matches) == 1
    assert matches[0].group("name") == "l5"
    assert matches[0].group("param") == "e"


def test_beta8_disables_unproductive_source_map_fetches() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    assert "MAX_SOURCE_MAPS = 0" in source


def test_beta8_remains_public_get_only_and_non_mutating() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert 'method="GET"' in source
    assert '"mutation_calls_executed": False' in source
    assert '"live_report_request_executed": False' in source
    assert "client.call(" not in source
    assert "Authorization" not in source
    assert "Cookie" not in source
    assert "0.4.3-beta8" in diagnostics
    assert "bounded read-only public-H5 inspection" in diagnostics
