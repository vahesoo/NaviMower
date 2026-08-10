"""Regression contracts carried forward from Navimower 0.4.1-beta21."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta21_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    version = manifest["version"]
    assert version.startswith("0.4.1-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 21
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta21.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta21")
    for phrase in (
        "Download diagnostics",
        "navimower.export_diagnostics",
        "notification_event_probe",
        "navimower_diagnostics_latest.json",
    ):
        assert phrase in notes


def test_action_export_still_writes_latest_and_state_capture() -> None:
    source = (COMPONENT / "action_diagnostics.py").read_text()
    ast.parse(source)
    assert '"navimower.export_diagnostics"' in source
    assert '"navimower_diagnostics_latest.json"' in source
    assert "state_transition_diagnostics" in source
    assert "async_build_diagnostics" in source


def test_export_service_still_uses_action_export() -> None:
    services = (COMPONENT / "services.py").read_text()
    assert "async_export_action_diagnostics" in services
    assert "async_export_diagnostics(" not in services


def test_legacy_action_probe_code_remains_read_only() -> None:
    source = (COMPONENT / "event_probe.py").read_text()
    for forbidden in (
        "/vehicle/set/send",
        "/vehicle/set/save-set-data",
        "/map/index/save",
        "save_setting(",
        "mow_zones(",
    ):
        assert forbidden not in source
