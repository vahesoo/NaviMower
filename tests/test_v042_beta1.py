"""Regression contracts for Navimower 0.4.2-beta1."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta1_manifest_notes_readme_and_changelog() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.2-beta1"

    notes = (ROOT / ".github" / "release-notes" / "0.4.2-beta1.md").read_text()
    assert notes.startswith("title: Navimower 0.4.2-beta1")
    for phrase in (
        "Legacy Map Camera",
        "clearBatchMessageRead",
        "Mark as read",
        "Mark all as read",
        "account-specific",
        "Download diagnostics",
    ):
        assert phrase in notes

    readme = (ROOT / "README.md").read_text()
    assert "removed from\n0.4.2-beta1 onward" in readme
    assert "account-specific" in readme
    assert "notification_read_h5_discovery" in readme
    assert "does not yet mark notifications read" in readme

    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert changelog.startswith("# Changelog\n\n## 0.4.2-beta1")
    assert "### Removed" in changelog
    assert "### Diagnostics safety" in changelog


def test_beta1_removes_only_legacy_map_camera_platform() -> None:
    setup = (COMPONENT / "__init__.py").read_text()
    runtime = (COMPONENT / "beta26_runtime.py").read_text()
    strings = json.loads((COMPONENT / "strings.json").read_text())
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())

    ast.parse(setup)
    ast.parse(runtime)

    assert not (COMPONENT / "camera.py").exists()
    assert "Platform.CAMERA" not in setup
    assert "NavimowMapCamera" not in runtime
    assert "_mark_map_camera_legacy" not in runtime
    assert "camera" not in strings.get("entity", {})
    assert "camera" not in english.get("entity", {})

    # Camera/VisionFence mower settings are unrelated to the removed SVG map camera.
    assert '"Camera positioning (EFLS)"' in (COMPONENT / "strings.json").read_text()


def test_beta1_keeps_notification_and_error_production_runtime() -> None:
    notification = (COMPONENT / "beta26_runtime.py").read_text()
    error = (COMPONENT / "beta16_runtime.py").read_text()
    ast.parse(notification)
    ast.parse(error)

    assert '"/mowerbot/user/message/vehicleMessageListField"' in notification
    assert 'name="Latest notification"' in notification
    assert '"read": _as_bool(' in notification
    assert "clearBatchMessageRead" not in notification

    assert '_STATE_FAULT = "0301"' in error
    assert '_STATE_LIFTED = "0302"' in error
    assert 'index2.get("error_data")' in error
    assert 'snapshot["error_title"] = live_error.get("title") or title' in error


def test_beta1_download_diagnostics_adds_targeted_read_only_h5_discovery() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    discovery = (COMPONENT / "notification_read_discovery.py").read_text()
    ast.parse(diagnostics)
    ast.parse(discovery)

    assert "from .notification_read_discovery import probe_notification_read_h5" in diagnostics
    assert 'document["notification_read_h5_discovery"]' in diagnostics
    assert "hass.async_add_executor_job" in diagnostics

    for target in (
        "clearBatchMessageRead",
        "vehicleMessageListField",
        "queryUnreadRedCountForVehicle",
        "getUnreadMessageAndRedCount",
    ):
        assert target in discovery

    assert 'method="GET"' in discovery
    assert '"mutation_calls_executed": False' in discovery
    assert '"public_unauthenticated_h5_only": True' in discovery
    assert "client.call(" not in discovery
    assert "send no Navimow" in discovery or "No token" in discovery


def test_beta1_does_not_restore_old_broad_discovery_stack() -> None:
    for removed in (
        "diagnostics_export.py",
        "action_diagnostics.py",
        "state_transition_capture.py",
        "event_probe.py",
        "event_transport_probe.py",
        "h5_discovery.py",
        "notification_feed_discovery.py",
    ):
        assert not (COMPONENT / removed).exists()
