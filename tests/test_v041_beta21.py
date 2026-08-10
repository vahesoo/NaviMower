"""Regression contracts for Navimower 0.4.1-beta21."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta21_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta21"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta21.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta21")
    for phrase in (
        "Download diagnostics",
        "navimower.export_diagnostics",
        "notification_event_probe",
        "navimower_diagnostics_latest.json",
    ):
        assert phrase in notes


def test_native_download_diagnostics_has_no_notification_probe() -> None:
    source = (COMPONENT / "diagnostics.py").read_text()
    ast.parse(source)
    assert "probe_event_endpoints" not in source
    assert '"notification_event_probe"' not in source
    assert '"home_assistant_download"' in source
    assert "state_transition_diagnostics" in source


def test_action_export_contains_notification_probe_and_writes_latest() -> None:
    source = (COMPONENT / "action_diagnostics.py").read_text()
    ast.parse(source)
    assert "probe_event_endpoints" in source
    assert 'document["notification_event_probe"]' in source
    assert '"navimower.export_diagnostics"' in source
    assert '"navimower_diagnostics_latest.json"' in source
    assert "state_transition_diagnostics" in source
    assert "async_build_diagnostics" in source


def test_export_service_uses_extended_action_export() -> None:
    services = (COMPONENT / "services.py").read_text()
    assert "async_export_action_diagnostics" in services
    assert "async_export_diagnostics(" not in services
    assert "includes notification/event discovery" in services


def test_action_probe_remains_read_only() -> None:
    source = (COMPONENT / "event_probe.py").read_text()
    for forbidden in (
        "/vehicle/set/send",
        "/vehicle/set/save-set-data",
        "/map/index/save",
        "save_setting(",
        "mow_zones(",
    ):
        assert forbidden not in source
