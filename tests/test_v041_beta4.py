"""Regression contracts for Navimower 0.4.1-beta4 MQTT recovery."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta4_version_and_notes():
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta4"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta4.md").read_text()
    assert "pose_degraded" in notes
    assert "120 seconds" in notes


def test_pose_recovery_does_not_rebuild_client():
    source = (COMPONENT / "mqtt.py").read_text()
    block = source.split("async def _async_recovery_cycle", 1)[1].split("async def _async_rebuild_client", 1)[0]
    assert "_async_rebuild_client" not in block
    assert 'self._set_recovery_state("pose_degraded", reason)' in block
    assert "MQTT_POSE_RESUBSCRIBE_COOLDOWN_SECONDS" in block


def test_current_device_message_age_is_not_account_wide():
    source = (COMPONENT / "mqtt.py").read_text()
    msg = source.split("async def _on_message", 1)[1].split("mqtt.on_connected", 1)[0]
    assert "if incoming_device_id == device_id:" in msg
    assert "self._last_any_message_mono = now" in msg
    assert '"last_any_message_scope": "current_device"' in source


def test_pose_resubscribe_cooldown_constant():
    source = (COMPONENT / "const.py").read_text()
    assert "MQTT_POSE_RESUBSCRIBE_COOLDOWN_SECONDS: Final = 120" in source
