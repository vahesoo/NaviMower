"""Regression contracts for Navimower 0.4.3-beta12 bounded diagnostics."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta12_release_identity() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta12"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta12.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta12")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.startswith("# Changelog\n\n## 0.4.3-beta12")


def test_beta12_error_discovery_is_wall_clock_bounded() -> None:
    source = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    ast.parse(source)
    for phrase in (
        "MAX_ROOT_REQUESTS = 4",
        "MAX_PREFIX_REQUESTS = 32",
        "MAX_FULL_MATCHES = 6",
        "MAX_PROBE_SECONDS = 24.0",
        "TIMEOUT = 2.5",
        "def _deadline_fetch",
        "wall_clock_budget_exhausted",
        "and not budget_exhausted",
        '"bounded_by_wall_clock": True',
        '"budget_exhausted": budget_exhausted',
        '"elapsed_seconds"',
    ):
        assert phrase in source


def test_beta12_prioritizes_proven_error_command_asset() -> None:
    source = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    assert "def _candidate_queue_key" in source
    assert '"source": "observed_error_command_asset"' in source
    assert '"priority": _priority(observed_url) + 5000' in source
    assert "queue = sorted(candidate_map.values(), key=_candidate_queue_key)" in source


def test_beta12_diagnostics_has_outer_timeout_fail_safe() -> None:
    source = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "ERROR_DISCOVERY_TIMEOUT_SECONDS = 30.0" in source
    assert "async with asyncio.timeout(ERROR_DISCOVERY_TIMEOUT_SECONDS):" in source
    assert '"timed_out": True' in source
    assert "public H5 error discovery exceeded the diagnostics timeout" in source


def test_beta12_notification_entity_history_is_recorder_safe() -> None:
    source = (COMPONENT / "notification_feed.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "_NOTIFICATION_ATTR_HISTORY_LIMIT = 5" in source
    assert "Recorder stays below its 16 KiB state-attribute limit" in source
    assert "MERGED_NOTIFICATION_LIMIT = LOCAL_NOTIFICATION_LIMIT + VENDOR_NOTIFICATION_LIMIT" in (COMPONENT / "notification_center.py").read_text(encoding="utf-8")


def test_beta12_discovery_remains_non_mutating() -> None:
    source = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    assert 'method="GET"' in source
    assert '"mutation_calls_executed": False' in source
    assert '"live_command_call_executed": False' in source
    assert '"notification_detail_call_executed": False' in source
    assert "client.call(" not in source
    assert "Authorization" not in source
    assert "Cookie" not in source
