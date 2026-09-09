"""Regression tests for conservative mowing-pause status semantics."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "custom_components" / "navimower" / "mowing_pause_status.py"
SPEC = importlib.util.spec_from_file_location("navimower_mowing_pause_status_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _task(paused: datetime) -> dict:
    return {
        "task_id": "task-1",
        "origin": "observed",
        "trigger": "observed_without_local_command",
        "progress_before_pause": 42.0,
        "battery_before_pause": 14.0,
        "charging_paused_at": _iso(paused),
        "charging_pause_confidence": "inferred_from_return_battery_threshold",
    }


def test_no_interruption_is_none() -> None:
    status = MODULE.classify_mowing_pause(
        interrupted_reason=None,
        active_task=None,
        settings={"return_battery_level": 15},
        vendor_messages=[],
    )
    assert status["state"] == "none"
    assert status["automation_safe_low_battery"] is False


def test_threshold_inference_alone_is_pending_not_automation_safe() -> None:
    paused = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    status = MODULE.classify_mowing_pause(
        interrupted_reason="charging",
        active_task=_task(paused),
        settings={"return_battery_level": 15},
        vendor_messages=[],
    )
    assert status["state"] == "low_battery_pending"
    assert status["confidence"] == "inferred_from_return_battery_threshold"
    assert status["low_battery_confirmed"] is False
    assert status["automation_safe_low_battery"] is False
    assert status["battery_before_pause"] == 14.0
    assert status["return_battery_level"] == 15.0


def test_matching_vendor_1502_confirms_low_battery_pause() -> None:
    paused = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    message_time = paused + timedelta(seconds=35)
    status = MODULE.classify_mowing_pause(
        interrupted_reason="charging",
        active_task=_task(paused),
        settings={"return_battery_level": 15},
        vendor_messages=[
            {
                "notification_code": "1502",
                "created_at": _iso(message_time),
            }
        ],
    )
    assert status["state"] == "low_battery"
    assert status["confidence"] == "vendor_reported"
    assert status["low_battery_confirmed"] is True
    assert status["automation_safe_low_battery"] is True
    assert status["vendor_confirmation_code"] == "1502"


def test_old_1502_does_not_confirm_a_new_manual_or_unknown_return() -> None:
    paused = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    old_message = paused - timedelta(hours=1)
    status = MODULE.classify_mowing_pause(
        interrupted_reason="charging",
        active_task=_task(paused),
        settings={"return_battery_level": 15},
        vendor_messages=[
            {
                "vendor_code": "1502",
                "created_at": _iso(old_message),
            }
        ],
    )
    assert status["state"] == "low_battery_pending"
    assert status["automation_safe_low_battery"] is False


def test_unrelated_vendor_notification_does_not_confirm_low_battery() -> None:
    paused = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    status = MODULE.classify_mowing_pause(
        interrupted_reason="charging",
        active_task=_task(paused),
        settings={"return_battery_level": 15},
        vendor_messages=[
            {
                "notification_code": "150A",
                "created_at": _iso(paused + timedelta(seconds=10)),
            }
        ],
    )
    assert status["state"] == "low_battery_pending"
    assert status["automation_safe_low_battery"] is False


def test_known_non_battery_reasons_remain_distinct() -> None:
    for reason, expected in (("night", "night"), ("manual_dock", "manual_dock"), ("unknown", "unknown")):
        status = MODULE.classify_mowing_pause(
            interrupted_reason=reason,
            active_task={},
            settings={},
            vendor_messages=[],
        )
        assert status["state"] == expected
        assert status["automation_safe_low_battery"] is False


def test_status_does_not_treat_generic_vehicle_states_as_low_battery_proof() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "STATE_RETURNING" not in source
    assert "MQTT_STATE_RETURNING" not in source
    assert "STATE_PAUSED" not in source
    assert "0211" not in source
    assert "0220" not in source
    assert "vendor Device\nnotification feed confirms" in source
