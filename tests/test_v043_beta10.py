"""Regression contracts for Navimower 0.4.3-beta10 error diagnostics."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta10_release_identity() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta10"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta10.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta10")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.startswith("# Changelog\n\n## 0.4.3-beta10")


def test_beta10_error_sensor_is_cloud_canonical() -> None:
    source = (COMPONENT / "state_semantics.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert 'value_fn=lambda data: data.get("error_text") or "No errors"' in source
    assert '"private_cloud_canonical_mqtt_transition_trigger"' in source
    assert 'transition = bool(state_name and state_name != previous_named)' in source
    assert 'state_name in {"Error", "Self-Checking"} or previous_named == "Error"' in source
    assert 'snapshot["error_text"] = "Error"' not in source
    assert 'snapshot["docked_source"] = "mqtt_error_state"' not in source


def test_beta10_retains_raw_vendor_notification_feed() -> None:
    source = (COMPONENT / "notification_feed.py").read_text(encoding="utf-8")
    assert 'coordinator._notification_raw_cache = deepcopy(response)' in source
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert '"vendor_notification_raw_cache"' in diagnostics
    assert '"vendor_notification_normalized_cache"' in diagnostics
    assert '"variable": deepcopy(data.get("notification_variable"))' in diagnostics


def test_beta10_diagnostics_focuses_only_error_action_discovery() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert "from .error_h5_discovery import probe_error_h5" in diagnostics
    assert "probe_error_h5," in diagnostics
    assert '"paused": True' in diagnostics
    assert '"error_investigation"' in diagnostics
    assert '"command_discovery": deepcopy(error_command_discovery)' in diagnostics
    assert "probe_maintenance_h5, coordinator.client" not in diagnostics


def test_beta10_error_h5_probe_is_strictly_read_only() -> None:
    source = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    ast.parse(source)
    for phrase in (
        "Clear and resume",
        "Reboot Mower",
        "clearError",
        "rebootMower",
        "/vehicle/set/send",
        "c:behavior",
        "cmdCode",
        "MAX_PREFIX_REQUESTS = 180",
        "PREFIX_BYTES = 64 * 1024",
        'method="GET"',
        '"mutation_calls_executed": False',
        '"live_command_call_executed": False',
        '"notification_detail_call_executed": False',
    ):
        assert phrase in source
    assert "client.call(" not in source
    assert "Authorization" not in source
    assert "Cookie" not in source


def test_beta10_error_probe_keeps_bounded_evidence() -> None:
    source = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    for phrase in (
        '"translation_keys"',
        '"matched_assets"',
        '"ui_contexts"',
        '"command_contexts"',
        '"prefix_request_count"',
        '"full_request_count"',
    ):
        assert phrase in source
