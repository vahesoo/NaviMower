"""Release regressions for Navimower 0.5.0-beta20."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta20_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta20"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta20.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "2,700 seconds",
        "120-second MQTT keepalive",
        "module-lock deadlock",
        "10 minutes",
        "get_cached_state()",
        "No Map Card update is required",
    ):
        assert marker in notes


def test_beta20_uses_upstream_confirmed_keepalive_and_transport_escalation() -> None:
    const = (COMPONENT / "const.py").read_text(encoding="utf-8")
    mqtt = (COMPONENT / "mqtt.py").read_text(encoding="utf-8")

    assert "MQTT_KEEPALIVE_SECONDS: Final = 120" in const
    assert "MQTT_TRANSPORT_RECOVERY_STALE_SECONDS: Final = 180" in const
    assert "keepalive_seconds=MQTT_KEEPALIVE_SECONDS" in mqtt
    assert "transport_age = self._transport_message_age()" in mqtt
    assert "if not sdk_connected or transport_stale:" in mqtt
    assert "await self._async_rebuild_client(transport_reason)" in mqtt
    assert "pose_resubscribe_streak" in mqtt
    ast.parse(mqtt)


def test_beta20_serializes_mower_sdk_import_off_event_loop() -> None:
    mqtt = (COMPONENT / "mqtt.py").read_text(encoding="utf-8")
    assert "_MOWER_SDK_IMPORT_LOCK = threading.Lock()" in mqtt
    loader = mqtt[
        mqtt.index("def _load_mower_sdk_types")
        : mqtt.index("class NavimowerMqttBridge")
    ]
    assert "with _MOWER_SDK_IMPORT_LOCK:" in loader
    assert "from mower_sdk.api import MowerAPI" in loader
    assert "from mower_sdk.sdk import NavimowSDK" in loader
    assert (
        "MowerAPI, NavimowSDK = await self.hass.async_add_executor_job(\n"
        "            _load_mower_sdk_types\n"
        "        )"
    ) in mqtt


def test_beta20_backs_off_mqtt_user_info_circuit_breaker() -> None:
    const = (COMPONENT / "const.py").read_text(encoding="utf-8")
    mqtt = (COMPONENT / "mqtt.py").read_text(encoding="utf-8")
    assert "MQTT_USER_INFO_RETRY_SECONDS: Final = 600" in const
    assert "self._retry_delay_hint_seconds = MQTT_USER_INFO_RETRY_SECONDS" in mqtt
    assert "hint > MQTT_START_RETRY_INITIAL_SECONDS" in mqtt
    assert "next_delay = max(next_delay, hint)" in mqtt


def test_beta20_preserves_runtime_mqtt_over_private_fetch_and_avoids_sdk_cache() -> None:
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    assert "get_cached_state(" not in coordinator
    assert "get_cached_attributes(" not in coordinator

    poll = coordinator[
        coordinator.index("    async def _async_update_data")
        : coordinator.index("    @staticmethod\n    def _poll_interval_for_snapshot")
    ]
    assert "snapshot = await self.hass.async_add_executor_job(self._fetch_blocking)" in poll
    assert "snapshot = self._apply_mqtt_snapshot(snapshot)" in poll
    assert poll.index("_fetch_blocking") < poll.index("_apply_mqtt_snapshot")
