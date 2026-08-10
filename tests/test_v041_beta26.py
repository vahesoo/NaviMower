"""Regression contracts for Navimower 0.4.1-beta26."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta26_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta26"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta26.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta26")
    for phrase in (
        "Notification",
        "get-vehicle-history-message",
        "60 seconds",
        "Download diagnostics",
        "does **not** mark notifications read",
    ):
        assert phrase in notes


def test_beta26_runtime_uses_exact_encrypted_history_contract() -> None:
    source = (COMPONENT / "beta26_runtime.py").read_text()
    ast.parse(source)
    for phrase in (
        '"/mowerbot/user/message/get-vehicle-history-message"',
        '"vehicle_sn": str(sn)',
        '"page": int(page)',
        '"size": int(size)',
        "self.call(",
        "_NOTIFICATION_TTL_SECONDS = 60",
        "_NOTIFICATION_PAGE_SIZE = 20",
    ):
        assert phrase in source
    assert "getmessageDetailResp" not in source
    assert "mark-read" not in source.lower()


def test_beta26_notification_sensor_is_bounded_and_last_good() -> None:
    source = (COMPONENT / "beta26_runtime.py").read_text()
    assert "_NOTIFICATION_ATTR_HISTORY_LIMIT = 5" in source
    for phrase in (
        'key="notification"',
        'name="Notification"',
        'icon="mdi:bell-outline"',
        '"notification_history"',
        '"notification_error"',
        '"private_cloud_message_history"',
        "_beta26_notification_cache",
    ):
        assert phrase in source
    failure = source[source.index("except Exception as err"):source.index("def _install_notification_sensor")]
    assert "_beta26_notification_error" in failure
    assert "_beta26_notification_cache = None" not in failure


def test_beta26_download_diagnostics_probes_exact_endpoint_not_h5_assets() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    action = (COMPONENT / "action_diagnostics.py").read_text()
    assert "notification_history_probe" in diagnostics
    assert "coordinator.client.notification_history" in diagnostics
    assert '"vehicle_sn": "<redacted>"' in diagnostics
    assert "inventory(clean)" in diagnostics
    assert "probe_h5_frontend" not in diagnostics
    assert "h5_frontend_discovery" not in diagnostics
    assert "notification_history_probe" not in action


def test_beta26_runtime_installs_before_sensor_platform_setup() -> None:
    services = (COMPONENT / "services.py").read_text()
    assert "from .beta26_runtime import install_beta26_runtime" in services
    assert "install_beta16_runtime()\ninstall_beta26_runtime()" in services
