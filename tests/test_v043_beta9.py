"""Regression contracts for Navimower 0.4.3-beta9 compact contract recovery."""
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
        assert isinstance(pattern, str)
        re.compile(pattern)
        rows[target.id] = (pattern, 0)
    return rows


def test_beta9_version_notes_and_changelog() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta9"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta9.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta9")
    assert "Compact discovery" in notes
    assert "strictly read-only" in notes
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.startswith("# Changelog\n\n## 0.4.3-beta9")


def test_beta9_reduces_crawl_and_output_budget() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    for phrase in (
        "MAX_ASSETS = 12",
        "MAX_TARGETED_ASSETS = 16",
        "MAX_TOTAL_REQUESTS = 64",
        "MAX_CONTEXTS = 48",
        "MAX_JS_CANDIDATES = 72",
        '"asset_evidence": [',
        "def _compact_asset_evidence",
        "def _compact_candidate",
    ):
        assert phrase in source
    assert '"assets": assets' not in source


def test_beta9_tightens_incidental_mowing_routing() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    assert 'term != "mowing"' in source
    assert '"mowing_records" in source_context' in source
    assert 'basename in OBSERVED_REPORT_ASSET_BASENAMES' in source
    assert '"index-594ad42d.js"' in source


def test_beta9_recovers_report_transport_wrappers() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    patterns = _compiled_patterns(source)
    assert "REPORT_TRANSPORT_ARROW_RE" in patterns
    regex = re.compile(patterns["REPORT_TRANSPORT_ARROW_RE"][0], re.I | re.S)
    sample = 'XH=e=>je.sendEncryptionData("handleEncipherment",e).then(a=>a),$H=e=>je.sendEncryptionData("handleDecrypt",e).then(a=>a);'
    matches = list(regex.finditer(sample))
    assert [(m.group("name"), m.group("method")) for m in matches] == [
        ("XH", "handleEncipherment"),
        ("$H", "handleDecrypt"),
    ]
    assert '"report_transport_wrapper_definitions"' in source


def test_beta9_has_cross_file_mower_set_alias_trace() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    patterns = _compiled_patterns(source)
    export_re = re.compile(patterns["EXPORT_BLOCK_RE"][0], re.I)
    import_re = re.compile(patterns["IMPORT_BLOCK_RE"][0], re.I)
    export_match = export_re.search('const l5=e=>e;export{l5 as ac,x as y};')
    assert export_match is not None and "l5 as ac" in export_match.group("bindings")
    import_match = import_re.search('import{ac as M,q as z}from"./app-entry.js";M({type:1});')
    assert import_match is not None and "ac as M" in import_match.group("bindings")
    for phrase in (
        "def _exported_aliases",
        "def _import_aliases_for_source",
        '"mower_set_export_aliases": mower_set_export_aliases',
        '"mower_set_import_aliases": mower_set_import_aliases',
        '"maintenance_mower_set_import_callsite"',
    ):
        assert phrase in source


def test_beta9_keeps_precise_mower_set_wrapper() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    patterns = _compiled_patterns(source)
    regex = re.compile(patterns["MOWER_SET_ARROW_WRAPPER_RE"][0], re.I)
    sample = '$H=(e={})=>je.sendEncryptionData("handleDecrypt",e),l5=(e={})=>je.callNative("handleH5MowerSet",e),x=1'
    match = regex.search(sample)
    assert match is not None
    assert match.group("name") == "l5"
    assert match.group("param") == "e"


def test_beta9_remains_public_get_only_and_non_mutating() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert 'method="GET"' in source
    assert '"mutation_calls_executed": False' in source
    assert '"live_report_request_executed": False' in source
    assert "client.call(" not in source
    assert "Authorization" not in source
    assert "Cookie" not in source
    assert "0.4.3-beta9" in diagnostics
    assert "bounded read-only public-H5 inspection" in diagnostics
