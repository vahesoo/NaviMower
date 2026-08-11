"""Regression contracts for Navimower 0.4.1-beta29."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta29_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta29"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta29.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta29")
    for phrase in (
        "vehicleMessageListField",
        "vehicle_message_list",
        "has_history_message",
        "filter_state",
        "Download diagnostics",
        "does not mark",
    ):
        assert phrase in notes


def test_beta29_runtime_uses_exact_main_device_feed_contract() -> None:
    source = (COMPONENT / "beta26_runtime.py").read_text()
    ast.parse(source)
    for phrase in (
        '"/mowerbot/user/message/vehicleMessageListField"',
        '"message_id": str(message_id or "")',
        '"vehicle_sn": str(sn)',
        '"filter_state": str(filter_state or _NOTIFICATION_FILTER)',
        'response.get("vehicle_message_list")',
        'response.get("has_history_message")',
        '"private_cloud_vehicle_message_feed"',
        '_NOTIFICATION_FILTER = "all"',
        "_NOTIFICATION_TTL_SECONDS = 60",
    ):
        assert phrase in source
    refresh = source[source.index("def _refresh_notification_cache"):source.index("def _install_notification_sensor")]
    assert "coordinator.client.notification_feed" in refresh
    assert "coordinator.client.notification_history" not in refresh


def test_beta29_sensor_stays_bounded_and_exposes_feed_metadata() -> None:
    source = (COMPONENT / "beta26_runtime.py").read_text()
    assert "_NOTIFICATION_ATTR_HISTORY_LIMIT = 5" in source
    for phrase in (
        'key="notification"',
        '"has_history_message"',
        '"next_message_id"',
        '"filter_state"',
        '"event_code"',
        '"recent"',
    ):
        assert phrase in source
    assert "clearBatchMessageRead" not in source
    assert "mark-read" not in source.lower()


def test_beta29_download_diagnostics_probes_only_exact_feed() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    ast.parse(diagnostics)
    for phrase in (
        '"/mowerbot/user/message/vehicleMessageListField"',
        'document["notification_feed_probe"]',
        "coordinator.client.notification_feed",
        '"message_id": ""',
        '"vehicle_sn": "<redacted>"',
        '"filter_state": "all"',
        "inventory(clean)",
    ):
        assert phrase in diagnostics
    assert "probe_main_notification_feed" not in diagnostics
    assert "notification_feed_discovery" not in diagnostics
    assert "notification_history_probe" not in diagnostics
    assert "clearBatchMessageRead" not in diagnostics


def test_beta29_action_export_remains_fast_and_without_notification_probe() -> None:
    action = (COMPONENT / "action_diagnostics.py").read_text()
    assert "notification_feed_probe" not in action
    assert "notification_feed_discovery" not in action
    assert "vehicleMessageListField" not in action
