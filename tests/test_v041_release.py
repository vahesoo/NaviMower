"""Stable release contracts for Navimower 0.4.1."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_v041_manifest_notes_readme_and_changelog() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1"

    notes = (ROOT / ".github" / "release-notes" / "0.4.1.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1\n\n")
    for phrase in (
        "Latest notification",
        "Problem",
        "Error",
        "experimental",
        "not yet been field-tested",
        "Legacy Map Camera",
        "0.4.2-beta1",
        "Download diagnostics",
    ):
        assert phrase in notes

    readme = (ROOT / "README.md").read_text()
    assert "### Entity reference and model support" in readme
    assert "Primary field testing has been performed on an **H215**" in readme
    assert "stable app/device identity" in readme
    assert "i2 AWD support is experimental" in readme
    assert "has not yet been field-tested" in readme
    assert "Legacy Map Camera is scheduled for removal in Navimower 0.4.2" in readme
    assert "0.4.2-beta1" in readme
    assert "navimower.export_diagnostics" in readme  # documented as removed

    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert changelog.startswith("# Changelog\n\n## 0.4.1")
    assert "### Added" in changelog
    assert "### Changed" in changelog
    assert "### Removed" in changelog
    assert "## 0.4.0" in changelog


def test_v041_keeps_production_notification_runtime() -> None:
    source = (COMPONENT / "beta26_runtime.py").read_text()
    ast.parse(source)
    assert '"/mowerbot/user/message/vehicleMessageListField"' in source
    assert '_NOTIFICATION_TTL_SECONDS = 60' in source
    assert '_NOTIFICATION_ATTR_HISTORY_LIMIT = 5' in source
    assert 'key="notification"' in source
    assert 'name="Latest notification"' in source
    assert '"notification_code": vendor_code' in source
    assert '"read": _as_bool(' in source
    assert 'clearBatchMessageRead' not in source

    normalize = source[
        source.index("def _normalize_item"):
        source.index("def _normalize_response")
    ]
    sensor = source[
        source.index("def _install_notification_sensor"):
        source.index("def _mark_map_camera_legacy")
    ]
    assert '"url"' not in normalize
    assert '"url"' not in sensor


def test_v041_keeps_numeric_error_and_lifted_semantics() -> None:
    source = (COMPONENT / "beta16_runtime.py").read_text()
    ast.parse(source)
    assert '_STATE_IDLE = "0103"' in source
    assert '_STATE_FAULT = "0301"' in source
    assert '_STATE_LIFTED = "0302"' in source
    assert 'index2.get("error_data")' in source
    assert 'snapshot["error_code"] = live_error.get("code")' in source
    assert 'snapshot["error_title"] = live_error.get("title") or title' in source
    assert 'snapshot["error_content"] = live_error.get("content")' in source
    assert 'snapshot["error_kind"] = "fault"' in source
    assert 'snapshot["error_code"] = None' in source
    assert 'snapshot["error_kind"] = "safety"' in source


def test_v041_keeps_i2_awd_support_but_documents_it_unverified() -> None:
    source = (COMPONENT / "beta17_runtime.py").read_text()
    ast.parse(source)
    assert '"i208 AWD"' in source
    assert 'key="eco_mode"' in source
    assert 'key="advanced_slope_mode"' in source
    assert 'key="progress_retention"' in source
    assert 'name="Global cutting height"' in source

    readme = (ROOT / "README.md").read_text()
    assert "i2 AWD experimental support" in readme
    assert "not yet been field-tested" in readme


def test_v041_keeps_legacy_switch_robot_first_cloud_second() -> None:
    source = (COMPONENT / "switch.py").read_text()
    ast.parse(source)
    assert "legacy_device_write: bool = False" in source
    for key in ("night_mow", "rain_detection", "rain_sensor"):
        marker = f'key="{key}"'
        start = source.index("NavimowSwitchDescription(", source.index(marker) - 100)
        end = source.index("    ),", source.index(marker)) + 6
        block = source[start:end]
        assert "legacy_device_write=True" in block
        assert "iot=True" not in block

    write = source[source.index("    async def _write"):source.index("    async def async_turn_on")]
    legacy = write[write.index("        else:"):]
    assert 'cloud_value = "01" if on else "00"' in legacy
    assert "self.coordinator.client.send_setting_device" in legacy
    assert "self.coordinator.client.set_bool_setting" in legacy
    assert legacy.index("self.coordinator.client.send_setting_device") < legacy.index(
        "self.coordinator.client.set_bool_setting"
    )


def test_v041_keeps_poll_guard_and_position_fallback() -> None:
    setup = (COMPONENT / "__init__.py").read_text()
    fallback = (COMPONENT / "beta18_runtime.py").read_text()
    ast.parse(setup)
    ast.parse(fallback)
    assert "async def _async_private_poll_guard" in setup
    assert "coordinator.private_poll_age()" in setup
    assert "private_poll_guard_task" in setup
    assert "report_time" in fallback
    assert "private_cloud" in fallback
    assert "mqtt" in fallback
    assert "two" in fallback.lower() or "2" in fallback


def test_v041_keeps_legacy_map_camera_until_042() -> None:
    camera = (COMPONENT / "camera.py").read_text()
    assert "class NavimowMapCamera" in camera
    assert 'NavimowEntity.__init__(self, coordinator, "map")' in camera

    runtime = (COMPONENT / "beta26_runtime.py").read_text()
    assert 'NavimowMapCamera._attr_name = "Legacy Map Camera"' in runtime
    assert "_mark_map_camera_legacy()" in runtime


def test_v041_removes_development_diagnostics_interface() -> None:
    services = (COMPONENT / "services.py").read_text()
    services_yaml = (COMPONENT / "services.yaml").read_text()
    options = (COMPONENT / "config_flow.py").read_text()
    init = (COMPONENT / "__init__.py").read_text()
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    sanitizer = (COMPONENT / "diagnostics_sanitize.py").read_text()

    ast.parse(services)
    ast.parse(options)
    ast.parse(init)
    ast.parse(diagnostics)
    ast.parse(sanitizer)

    assert "SERVICE_EXPORT_DIAGNOSTICS" not in services
    assert "SERVICE_MARK_DISCOVERY_EVENT" not in services
    assert "install_state_transition_capture" not in services
    assert "export_diagnostics:" not in services_yaml
    assert "mark_discovery_event:" not in services_yaml

    assert "OPT_PASSIVE_DISCOVERY" not in options
    assert "OPT_DIAGNOSTICS_DETAIL" not in options
    assert '_DEPRECATED_DIAGNOSTICS_OPTIONS = {"diagnostics_detail", "passive_discovery"}' in init

    assert "async_build_diagnostics" not in diagnostics
    assert "diagnostic_discovery" not in diagnostics
    assert "discovery_inventory" not in diagnostics
    assert "command_status" not in diagnostics
    assert '"diagnostics_source": "home_assistant_download"' in diagnostics
    assert '"latest_notification"' in diagnostics
    assert '"problem_history"' in diagnostics
    assert '"raw"' in diagnostics
    assert "No mower commands, settings writes, discovery probes or extra vendor requests" in diagnostics
    assert "from .diagnostics_sanitize import sanitize" in diagnostics
    assert "def sanitize" in sanitizer
    assert not (COMPONENT / "diagnostics_export.py").exists()

    for removed in (
        "action_diagnostics.py",
        "state_transition_capture.py",
        "event_probe.py",
        "event_transport_probe.py",
        "h5_discovery.py",
        "notification_feed_discovery.py",
    ):
        assert not (COMPONENT / removed).exists()
