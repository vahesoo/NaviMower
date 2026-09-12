"""Apply staged custom-queue edits exactly at scheduler round boundaries."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from homeassistant.util import dt as dt_util

from .const import (
    ACTIVITY_DOCKED,
    ACTIVITY_PAUSED,
    SCHEDULE_MODE_CONTINUOUS,
    SCHEDULE_ORDER_CUSTOM,
)
from .navimower_schedule import NavimowerScheduleController, _utc_now
from .schedule_queue_semantics import (
    _configured_round_queue,
    _execution_queue,
    _slot_set,
)

_INSTALLED = False
_ORIGINAL_EVALUATE_LOCKED: Callable[..., Awaitable[None]] | None = None


def _custom_round_complete(controller: NavimowerScheduleController) -> bool:
    queue = _execution_queue(controller)
    if not queue:
        return False
    completed = _slot_set(controller._runtime.get("completed_queue_slots"))
    return all(slot in completed for slot in range(len(queue)))


def _prepare_next_continuous_round(controller: NavimowerScheduleController) -> bool:
    """Roll a completed 24-hour round before the first dispatch of the next one."""
    if controller._mode != SCHEDULE_MODE_CONTINUOUS:
        return False
    if controller._order_mode != SCHEDULE_ORDER_CUSTOM:
        return False
    if not _custom_round_complete(controller):
        return False
    data = controller.coordinator.data or {}
    if data.get("activity") not in {ACTIVITY_DOCKED, ACTIVITY_PAUSED}:
        return False
    if controller._runtime.get("active_zone_id") is not None:
        return False
    if isinstance(controller._runtime.get("pending_command"), dict):
        return False
    if controller._runtime.get("resume_pending"):
        return False

    controller._runtime["round_queue"] = _configured_round_queue(controller)
    controller._runtime["started_queue_slots"] = []
    controller._runtime["completed_queue_slots"] = []
    controller._runtime["active_queue_slot"] = None
    controller._runtime["completed_zone_ids_in_window"] = []
    controller._runtime["just_completed_zone_id"] = None
    controller._runtime["round_index"] = int(
        controller._runtime.get("round_index") or 1
    ) + 1
    controller._runtime["round_started_at"] = _utc_now()
    controller._runtime["last_command"] = (
        f"round_queue_snapshot:{controller._runtime['round_index']}"
    )
    controller._runtime["last_command_at"] = _utc_now()
    controller._runtime["last_error"] = None
    return True


def _prepare_new_window_snapshot(controller: NavimowerScheduleController) -> bool:
    """Use the latest editor queue before a new daily window can dispatch slot 0."""
    if controller._order_mode != SCHEDULE_ORDER_CUSTOM:
        return False
    in_window, token = controller._window_state(dt_util.now())
    if not in_window or not token or token == controller._runtime.get("window_token"):
        return False
    controller._runtime["round_queue"] = _configured_round_queue(controller)
    controller._runtime["started_queue_slots"] = []
    return True


async def _evaluate_locked(self: NavimowerScheduleController) -> None:
    changed = _prepare_new_window_snapshot(self)
    changed = _prepare_next_continuous_round(self) or changed
    if changed:
        await self._save()

    assert _ORIGINAL_EVALUATE_LOCKED is not None
    await _ORIGINAL_EVALUATE_LOCKED(self)


def install_schedule_queue_boundary_semantics() -> None:
    """Install staged-queue boundary handling after positional queue semantics."""
    global _INSTALLED, _ORIGINAL_EVALUATE_LOCKED
    if _INSTALLED:
        return
    _ORIGINAL_EVALUATE_LOCKED = NavimowerScheduleController._evaluate_locked
    NavimowerScheduleController._evaluate_locked = _evaluate_locked
    _INSTALLED = True
