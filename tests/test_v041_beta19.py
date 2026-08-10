"""Regression contracts for Navimower 0.4.1-beta19."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta19_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    version = manifest["version"]
    assert version.startswith("0.4.1-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 19
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta19.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta19")
    for phrase in (
        "notification_event_probe",
        "24 likely read-only",
        "normal coordinator polling is unchanged",
        "business code",
    ):
        assert phrase in notes


def test_event_probe_is_bounded_and_read_only() -> None:
    source = (COMPONENT / "event_probe.py").read_text()
    ast.parse(source)
    assert "_EVENT_PATHS" in source
    assert source.count('"/message/') >= 6
    assert source.count('"/push/') >= 2
    for forbidden in (
        "/vehicle/set/send",
        "/vehicle/set/save-set-data",
        "/map/index/save",
        "save_setting(",
        "mow_zones(",
    ):
        assert forbidden not in source
    assert '"normal_polling_unchanged": True' in source


def test_event_probe_uses_broad_parameter_aliases() -> None:
    source = (COMPONENT / "event_probe.py").read_text()
    for key in (
        '"vehicle_sn"',
        '"vehicle_type"',
        '"page"',
        '"pageNum"',
        '"pageNo"',
        '"pageSize"',
        '"current"',
        '"size"',
        '"limit"',
        '"offset"',
        '"eventType"',
        '"messageType"',
        '"readStatus"',
    ):
        assert key in source
    assert '"account_minimal"' in source
    assert '"device_extended"' in source


def test_native_diagnostics_executes_probe_only_on_export() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    coordinator = (COMPONENT / "coordinator.py").read_text()
    assert "probe_event_endpoints" in diagnostics
    assert 'document["notification_event_probe"]' in diagnostics
    assert "probe_event_endpoints" not in coordinator
    assert "async_add_executor_job" in diagnostics
