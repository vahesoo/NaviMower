"""Regression contracts for Navimower 0.4.3-beta5 targeted H5 call-site recovery."""
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
    assert manifest["version"].startswith("0.4.3")
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta5.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta5")
    assert "targeted call-site recovery" in notes
    assert "16 additional successful JavaScript fetches" in notes
    assert "strictly read-only" in notes
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## 0.4.3-beta5" in changelog


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
        "MAX_ASSETS =",
        "MAX_TARGETED_ASSETS =",
        "MAX_REQUESTS =",
        "targeted_priority_reserve",
        "def _is_targeted_candidate",
        "targeted_queue",
        '"targeted_fetches": targeted_fetches',
        '"targeted_fetch_count": len(targeted_fetches)',
        '"targeted_success_count": targeted_success',
        "def _named_callsite_contexts",
        "def _callsite_findings",
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
    assert "bounded read-only public-H5 inspection" in diagnostics
    assert (
        "targeted" in diagnostics.lower()
        or "compact" in diagnostics.lower()
        or "call-site" in diagnostics.lower()
    )
