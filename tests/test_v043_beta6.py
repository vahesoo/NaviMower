"""Regression contracts for Navimower 0.4.3-beta6 and later releases."""
from __future__ import annotations

import ast
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


def test_beta6_release_notes_and_changelog() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta6.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta6")
    assert "Parts maintenance" in notes
    assert "source-map" in notes
    assert "strictly read-only" in notes
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## 0.4.3-beta6" in changelog


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
        "MAX_TARGETED_ASSETS",
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
        "MAX_SOURCE_MAPS",
        "MAX_SOURCE_MAP",
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
    assert "0.4.3-beta" in diagnostics
    assert '"maintenance_h5_discovery"' in diagnostics
