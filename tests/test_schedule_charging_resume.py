"""Regression guards for managed-schedule low-battery charging resume."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "custom_components" / "navimower" / "navimower_schedule.py").read_text(
    encoding="utf-8"
)


def _async_function(name: str) -> str:
    start = SOURCE.index(f"    async def {name}(")
    next_async = SOURCE.find("\n    async def ", start + 1)
    next_sync = SOURCE.find("\n    def ", start + 1)
    candidates = [value for value in (next_async, next_sync) if value >= 0]
    end = min(candidates) if candidates else len(SOURCE)
    return SOURCE[start:end]


def test_schedule_module_stays_syntax_valid() -> None:
    ast.parse(SOURCE)


def test_vendor_confirmed_low_battery_interrupt_retains_active_zone() -> None:
    evaluate = _async_function("_evaluate_locked")
    assert "_charging_interruption_confirmed()" in evaluate
    assert "await self._capture_charging_interruption()" in evaluate
    assert "and not self._vendor_mowing(data)" in evaluate

    helper = _async_function("_capture_charging_interruption")
    assert 'self._runtime["resume_pending"] = True' in helper
    assert 'self._runtime["interrupted_reason"] = "low_battery"' in helper
    assert 'self._runtime["interrupted_zone_id"] = int(zone_id)' in helper
    assert 'self._runtime["interrupted_cycle_id"]' in helper
    assert 'self._runtime["progress_before_interrupt"]' in helper
    assert 'self._runtime["charging_limit_reached_at"] = None' in helper
    assert 'self._runtime["last_command"] = f"charging_pause:{zone_id}"' in helper


def test_low_battery_waits_for_user_charging_limit_not_charging_state() -> None:
    helper = _async_function("_evaluate_low_battery_resume")
    assert 'battery = _as_int(data.get("battery"))' in helper
    assert "limit = self._charging_limit_percent(data)" in helper
    assert 'battery < limit' in helper
    assert 'self._runtime["charging_limit_reached_at"]' in helper
    assert "_LOW_BATTERY_RESUME_GRACE_SECONDS" in helper
    assert "_vendor_charging" not in helper
    assert 'source="navimower_schedule_charge_limit_fallback"' in helper
    assert 'continue_source="navimower_schedule_charge_limit_continue_fallback"' in helper


def test_charge_limit_fallback_rechecks_window_immediately_before_resume() -> None:
    helper = _async_function("_evaluate_low_battery_resume")
    assert "fresh_data = self.coordinator.data or {}" in helper
    assert "if not self._window_open_now():" in helper
    assert helper.index("if not self._window_open_now():") < helper.index(
        "await self._continue_interrupted_task("
    )

    cont = _async_function("_continue_interrupted_task")
    assert cont.count("if not self._window_open_now():") >= 2
    assert "await async_resume_task(self.coordinator, source=source)" in cont
    assert "reset=False, source=continue_source" in cont
    assert "reset=True" not in cont


def test_window_and_low_battery_interruptions_have_separate_reasons() -> None:
    closed = _async_function("_enforce_closed_window")
    assert 'self._runtime["interrupted_reason"] = "window_closed"' in closed
    assert '"low_battery" if self._charging_interruption_confirmed() else "window_closed"' in closed

    evaluate = _async_function("_evaluate_locked")
    assert 'self._runtime.get("interrupted_reason") == "low_battery"' in evaluate
    assert 'source="navimower_schedule_window_resume"' in evaluate
    assert 'continue_source="navimower_schedule_window_continue_fallback"' in evaluate


def test_mower_self_resume_is_vendor_confirmed_and_clears_fallback() -> None:
    evaluate = _async_function("_evaluate_locked")
    resume_block = evaluate[evaluate.index('if self._runtime.get("resume_pending"):'):]
    assert "if self._vendor_mowing(data):" in resume_block
    assert "self._clear_interruption_runtime()" in resume_block
    assert 'self._runtime["last_command"] = "retained_task_already_mowing"' in resume_block

    confirm = _async_function("_confirm_pending")
    assert 'kind in {"resume", "continue"} and self._vendor_mowing(data)' in confirm


def test_only_notification_center_charging_reason_arms_low_battery_wait() -> None:
    start = SOURCE.index("    def _charging_interruption_confirmed")
    end = SOURCE.index("    async def _capture_charging_interruption", start)
    helper = SOURCE[start:end]
    assert 'getattr(center, "interrupted_reason", None) == "charging"' in helper
    assert "return_battery_level" not in helper
    assert "manual_dock" not in helper


def test_beta46_persisted_charging_resume_is_migrated_without_immediate_resume() -> None:
    evaluate = _async_function("_evaluate_locked")
    assert 'self._runtime.get("interrupted_reason") is None' in evaluate
    assert 'self._runtime["interrupted_reason"] = "low_battery"' in evaluate
    assert 'self._runtime["charging_limit_reached_at"] = None' in evaluate
