"""Regression contracts for Navimower 0.4.3-beta4 and later releases."""
from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta4_release_notes_and_focus() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta4.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta4")
    assert "Maintenance + Mowing Reports" in notes
    assert "content-hash" in notes
    assert "strictly read-only" in notes
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## 0.4.3-beta4" in changelog


def test_beta4_discovery_fixes_duplicate_asset_paths() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    assert '"/static/js/static/js/"' in source
    assert '"/assets/assets/"' in source
    assert "def _resolve_js_url" in source
    assert 'for marker in ("static/js/", "assets/")' in source
    assert "MAX_ASSETS" in source
    assert "MAX_REQUESTS" in source
    assert 'row["counts_toward_asset_limit"] = counts_toward_limit' in source


def test_beta4_targets_report_contracts_and_hashed_chunks_semantically() -> None:
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
        "def _filename_bonus",
        "def _candidate_score",
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
    assert "0.4.3-beta" in diagnostics
    assert "maintenance" in diagnostics.lower()
    assert "Mowing Reports" in diagnostics
    assert '"maintenance_h5_discovery": maintenance_h5_discovery' in diagnostics
