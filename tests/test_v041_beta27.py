"""Regression contracts for Navimower 0.4.1-beta27."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta27_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta27"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta27.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta27")
    for phrase in (
        "All",
        "Important",
        "Work status",
        "System",
        "Device",
        "sendEncryptionData",
        "/mowerbot/",
        "Download diagnostics",
    ):
        assert phrase in notes


def test_beta27_targeted_notification_feed_scanner_contract() -> None:
    source = (COMPONENT / "notification_feed_discovery.py").read_text()
    ast.parse(source)
    for phrase in (
        '"All"',
        '"Important"',
        '"Work status"',
        '"System"',
        '"Device"',
        '"newMessages"',
        '"No more messages"',
        '"Failed to load new messages"',
        'r"[\\\"\'](/mowerbot/',
        "sendEncryptionData",
        "callNative",
        "sendMessageToNative",
        "_MAX_DYNAMIC_ASSETS = 6",
        "public_unauthenticated_only",
    ):
        assert phrase in source
    assert "access_token" not in source
    assert "vehicle_sn" not in source
    assert "device_id" not in source


def test_beta27_download_diagnostics_runs_both_history_and_targeted_discovery() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    assert "notification_history_probe" in diagnostics
    assert "notification_feed_discovery" in diagnostics
    assert "probe_main_notification_feed" in diagnostics
    assert "coordinator.client.notification_history" in diagnostics


def test_beta27_action_export_remains_without_h5_discovery() -> None:
    action = (COMPONENT / "action_diagnostics.py").read_text()
    assert "probe_main_notification_feed" not in action
    assert "notification_feed_discovery" not in action
