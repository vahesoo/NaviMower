"""Regression contracts for Navimower 0.4.2-beta2."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta2_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.2-beta2"

    notes = (ROOT / ".github" / "release-notes" / "0.4.2-beta2.md").read_text()
    assert notes.startswith("title: Navimower 0.4.2-beta2")
    for phrase in (
        "mark_notification_read",
        "mark_all_notifications_read",
        "clearBatchMessageRead",
        "getmessageDetailResp",
        "searchMessageStatus: false",
        "account",
        "snapshot-only",
    ):
        assert phrase in notes


def test_beta2_notification_actions_match_recovered_h5_contracts() -> None:
    source = (COMPONENT / "notification_actions.py").read_text()
    ast.parse(source)

    assert '"/mowerbot/user/message/clearBatchMessageRead"' in source
    assert '"/mowerbot/user/message/getmessageDetailResp"' in source
    assert '_DEVICE_MESSAGE_DETAIL_TYPE = 2' in source
    assert '"message_id": message_id' in source
    assert '"type": _DEVICE_MESSAGE_DETAIL_TYPE' in source
    assert '"vehicle_sn": str(coordinator.sn)' in source
    assert '"searchMessageStatus": False' in source

    # The write/detail call is explicit and followed by a forced authoritative
    # Device-feed refresh. Do not optimistically rewrite notification cache rows.
    assert "coordinator.client.call" in source
    assert "coordinator._beta26_notification_last_attempt_mono = None" in source
    assert "await coordinator.async_request_refresh()" in source
    assert "_beta26_notification_cache" not in source


def test_beta2_exposes_two_notification_services() -> None:
    source = (COMPONENT / "services.py").read_text()
    yaml_source = (COMPONENT / "services.yaml").read_text()
    ast.parse(source)

    assert 'SERVICE_MARK_NOTIFICATION_READ = "mark_notification_read"' in source
    assert 'SERVICE_MARK_ALL_NOTIFICATIONS_READ = "mark_all_notifications_read"' in source
    assert "async_mark_notification_read" in source
    assert "async_mark_all_notifications_read" in source
    assert "MARK_NOTIFICATION_READ_SCHEMA" in source
    assert "MARK_ALL_NOTIFICATIONS_READ_SCHEMA" in source
    assert "if not hass.services.has_service(DOMAIN, SERVICE_MARK_NOTIFICATION_READ)" in source
    assert "if not hass.services.has_service(DOMAIN, SERVICE_MARK_ALL_NOTIFICATIONS_READ)" in source

    assert "mark_notification_read:" in yaml_source
    assert "mark_all_notifications_read:" in yaml_source
    assert "Message ID" in yaml_source
    assert "account" in yaml_source


def test_beta2_removes_h5_discovery_and_restores_snapshot_only_diagnostics() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    ast.parse(diagnostics)

    assert not (COMPONENT / "notification_read_discovery.py").exists()
    assert "probe_notification_read_h5" not in diagnostics
    assert "notification_read_h5_discovery" not in diagnostics
    assert "async_add_executor_job" not in diagnostics
    assert "makes no extra vendor or public-H5 requests" in diagnostics
    assert "Notification read actions are explicit Home Assistant services" in diagnostics


def test_beta2_keeps_normal_notification_poller_non_mutating() -> None:
    runtime = (COMPONENT / "beta26_runtime.py").read_text()
    ast.parse(runtime)

    assert '"/mowerbot/user/message/vehicleMessageListField"' in runtime
    assert 'name="Latest notification"' in runtime
    assert '"read": _as_bool(' in runtime
    assert "clearBatchMessageRead" not in runtime
    assert "getmessageDetailResp" not in runtime


def test_beta2_keeps_legacy_map_camera_removed() -> None:
    setup = (COMPONENT / "__init__.py").read_text()
    assert not (COMPONENT / "camera.py").exists()
    assert "Platform.CAMERA" not in setup
