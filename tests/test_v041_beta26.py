"""Regression contracts introduced in Navimower 0.4.1-beta26."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _beta_number() -> int:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    return int(manifest["version"].split("beta", 1)[1])


def test_beta26_release_contract_remains_present() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"].startswith("0.4.1-beta")
    assert int(manifest["version"].split("beta", 1)[1]) >= 26
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
        "_beta26_notification_cache",
    ):
        assert phrase in source
    if _beta_number() >= 29:
        assert '"private_cloud_vehicle_message_feed"' in source
    else:
        assert '"private_cloud_message_history"' in source
    failure = source[source.index("except Exception as err"):source.index("def _install_notification_sensor")]
    assert "_beta26_notification_error" in failure
    assert "_beta26_notification_cache = None" not in failure


def test_beta26_download_diagnostics_contract_is_superseded_safely() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    action = (COMPONENT / "action_diagnostics.py").read_text()
    if _beta_number() >= 30:
        assert "notification_feed_probe" not in diagnostics
        assert "coordinator.client.notification_feed" not in diagnostics
        assert "notification_history_probe" not in diagnostics
        assert "probe_main_notification_feed" not in diagnostics
        assert "inventory(clean)" not in diagnostics
    elif _beta_number() >= 29:
        assert "notification_feed_probe" in diagnostics
        assert "coordinator.client.notification_feed" in diagnostics
        assert "notification_history_probe" not in diagnostics
        assert "probe_main_notification_feed" not in diagnostics
        assert '"vehicle_sn": "<redacted>"' in diagnostics
        assert "inventory(clean)" in diagnostics
    else:
        assert "notification_history_probe" in diagnostics
        assert "coordinator.client.notification_history" in diagnostics
        assert '"vehicle_sn": "<redacted>"' in diagnostics
        assert "inventory(clean)" in diagnostics
    assert "notification_feed_probe" not in action
    assert "notification_history_probe" not in action


def test_beta26_runtime_installs_before_sensor_platform_setup() -> None:
    services = (COMPONENT / "services.py").read_text()
    assert "from .beta26_runtime import install_beta26_runtime" in services
    assert "install_beta16_runtime()\ninstall_beta26_runtime()" in services
