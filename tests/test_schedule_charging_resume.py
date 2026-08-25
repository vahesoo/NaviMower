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


def test_vendor_confirmed_charging_interrupts_active_scheduler_zone() -> None:
    evaluate = _async_function("_evaluate_locked")
    assert "_charging_interruption_confirmed()" in evaluate
    assert "await self._capture_charging_interruption()" in evaluate
    capture = evaluate.index("await self._capture_charging_interruption()")
    active_return = evaluate.index('if self._runtime.get("active_zone_id") is not None:')
    assert capture < active_return

    helper = _async_function("_capture_charging_interruption")
    assert 'self._runtime["resume_pending"] = True' in helper
    assert 'self._runtime["interrupted_zone_id"] = int(zone_id)' in helper
    assert 'self._runtime["interrupted_cycle_id"]' in helper
    assert 'self._runtime["progress_before_interrupt"]' in helper
    assert 'self._runtime["last_command"] = f"charging_pause:{zone_id}"' in helper


def test_charging_waits_for_vendor_to_leave_charging_before_resume() -> None:
    evaluate = _async_function("_evaluate_locked")
    resume_block = evaluate[evaluate.index('if self._runtime.get("resume_pending"):'):]
    assert "if self._vendor_charging(data):" in resume_block
    wait = resume_block.index("if self._vendor_charging(data):")
    resume = resume_block.index("await self._continue_interrupted_task(activity)")
    assert wait < resume


def test_only_notification_center_charging_reason_auto_arms_resume() -> None:
    start = SOURCE.index("    def _charging_interruption_confirmed")
    end = SOURCE.index("    async def _capture_charging_interruption", start)
    helper = SOURCE[start:end]
    assert 'getattr(center, "interrupted_reason", None) == "charging"' in helper
    assert "return_battery_level" not in helper
    assert "manual_dock" not in helper


def test_retained_task_resume_never_resets_the_zone() -> None:
    cont = _async_function("_continue_interrupted_task")
    assert 'reset=False, source="navimower_schedule_continue_fallback"' in cont
    assert "reset=True" not in cont
