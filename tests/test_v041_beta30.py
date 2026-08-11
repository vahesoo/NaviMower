"""Regression contracts for Navimower 0.4.1-beta30."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta30_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta30"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta30.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta30")
    for phrase in (
        "150A",
        "notification_code",
        "vendor_code",
        "boolean",
        "style",
        "url",
        "Download diagnostics",
    ):
        assert phrase in notes


def test_beta30_notification_codes_are_preserved_as_strings() -> None:
    source = (COMPONENT / "beta26_runtime.py").read_text()
    ast.parse(source)
    normalize = source[
        source.index("def _normalize_item"):
        source.index("def _normalize_response")
    ]
    assert '"error_code"' in normalize
    assert '"errorCode"' in normalize
    assert '"notification_code": vendor_code' in normalize
    assert '"vendor_code": vendor_code' in normalize
    assert '"error_code": vendor_code' in normalize
    assert '"event_code": vendor_code' in normalize
    assert "_code_text(" in normalize
    assert "_as_int(" not in normalize.split('"level"', 1)[0]


def test_beta30_preserves_real_feed_metadata() -> None:
    source = (COMPONENT / "beta26_runtime.py").read_text()
    for phrase in (
        '"read": _as_bool(',
        '"style": _first_present(',
        '"url": _bounded_text(',
        '"variable": deepcopy(',
        '"notification_style"',
        '"notification_url"',
        '"notification_variable"',
        '"notification_code"',
        '"notification_vendor_code"',
        '"notification_error_code"',
    ):
        assert phrase in source


def test_beta30_sensor_exposes_new_schema_and_compat_alias() -> None:
    source = (COMPONENT / "beta26_runtime.py").read_text()
    sensor = source[source.index("def _install_notification_sensor"):]
    for phrase in (
        '"notification_code"',
        '"vendor_code"',
        '"error_code"',
        '"event_code"',
        '"read"',
        '"style"',
        '"url"',
        '"variable"',
        '"recent"',
    ):
        assert phrase in sensor
    assert "_NOTIFICATION_ATTR_HISTORY_LIMIT = 5" in source
    assert "clearBatchMessageRead" not in source


def test_beta30_download_diagnostics_no_longer_probes_notifications() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    ast.parse(diagnostics)
    assert "notification_feed_probe" not in diagnostics
    assert "vehicleMessageListField" not in diagnostics
    assert "notification_feed_discovery" not in diagnostics
    assert "notification_history_probe" not in diagnostics
    assert "coordinator.client.notification_feed" not in diagnostics
    assert "inventory(" not in diagnostics
    assert 'document["diagnostics_source"] = "home_assistant_download"' in diagnostics


def test_beta30_action_export_stays_without_notification_probe() -> None:
    action = (COMPONENT / "action_diagnostics.py").read_text()
    assert "notification_feed_probe" not in action
    assert "vehicleMessageListField" not in action
