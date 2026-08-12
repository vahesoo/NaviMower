"""Regression contracts for Navimower 0.4.3-beta7 targeted request reserve."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta7_version_notes_and_changelog() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta7"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta7.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta7")
    assert "targeted" in notes.lower()
    assert "strictly read-only" in notes
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.startswith("# Changelog\n\n## 0.4.3-beta7")


def test_beta7_separates_broad_and_targeted_request_budgets() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    for phrase in (
        "MAX_BROAD_REQUESTS = 104",
        "MAX_TARGETED_REQUESTS = 56",
        "MAX_TOTAL_REQUESTS = 168",
        "broad_request_count < MAX_BROAD_REQUESTS",
        "targeted_request_count < MAX_TARGETED_REQUESTS",
        "request_count < MAX_TOTAL_REQUESTS",
        "broad_request_count += 1",
        "targeted_request_count += 1",
    ):
        assert phrase in source


def test_beta7_targeted_phase_is_observable_and_runs_before_source_maps() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    for phrase in (
        "targeted_queue_initial_count = len(targeted_queue)",
        '"broad_request_count": broad_request_count',
        '"targeted_request_count": targeted_request_count',
        '"targeted_queue_initial_count": targeted_queue_initial_count',
        '"targeted_request_reserve_exhausted":',
        '"source": candidate.get("source")',
        '"theme_terms": candidate.get("theme_terms") or []',
    ):
        assert phrase in source
    assert source.index("targeted_queue_initial_count = len(targeted_queue)") < source.index("source_map_success = 0")


def test_beta7_deprioritizes_source_maps_after_beta6_404s() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    assert "MAX_SOURCE_MAPS = 2" in source
    assert "source_map_request_count += 1" in source
    assert source.index("while (\n        targeted_queue") < source.index("for map_candidate in source_map_rows[:MAX_SOURCE_MAPS]")


def test_beta7_adds_observed_parts_maintenance_correlation_anchors() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    for phrase in (
        '"Time to clean your mower"',
        '"Maintenance point reached"',
        '"review parts usage"',
        '"start cleaning"',
        '"reset the timer"',
        '"handleH5MowerSet"',
    ):
        assert phrase in source


def test_beta7_remains_public_get_only_and_non_mutating() -> None:
    source = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert 'method="GET"' in source
    assert '"mutation_calls_executed": False' in source
    assert '"live_report_request_executed": False' in source
    assert "client.call(" not in source
    assert "Authorization" not in source
    assert "Cookie" not in source
    assert "0.4.3-beta7" in diagnostics
    assert "bounded read-only public-H5 inspection" in diagnostics
