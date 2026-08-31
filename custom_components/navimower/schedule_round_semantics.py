"""Safe round rollover semantics for Navimower Schedule.

Direct handoff remains useful between zones inside one scheduler round. Starting
the first zone of a new round is different: a fresh ``reset=true`` command sent
while the mower still reports mowing/returning can race the vendor's task-finish
transition. Therefore every round boundary waits for a normal idle start state.

24-hour mode already repeated rounds. Time-window mode now does the same while
the window remains open: once every selected zone/queue slot completes, a new
round is prepared and starts after the mower reaches Docked/Paused. The window
end remains the hard boundary.
"""
from __future__ import annotations

from homeassistant.util import dt as dt_util

from .const import (
    ACTIVITY_MOWING,
    ACTIVITY_RETURNING,
    SCHEDULE_MODE_CONTINUOUS,
    SCHEDULE_MODE_WINDOW,
    SCHEDULE_ORDER_CUSTOM,
)
from .navimower_schedule import NavimowerScheduleController, _utc_now

_INSTALLED = False
_ORIGINAL_CONFIRM_ACTIVE_COMPLETION = NavimowerScheduleController._confirm_active_completion
_ORIGINAL_ASYNC_SEND_MOW = NavimowerScheduleController._async_send_mow
_ORIGINAL_EVALUATE_LOCKED = NavimowerScheduleController._evaluate_locked


def _continuous_round_complete(controller: NavimowerScheduleController) -> bool:
    """Return whether the current automatic/custom round is fully complete."""
    if controller._mode not in {SCHEDULE_MODE_CONTINUOUS, SCHEDULE_MODE_WINDOW}:
        return False

    eligible = controller._eligible_zones()
    if not eligible:
        return False

    if controller._order_mode == SCHEDULE_ORDER_CUSTOM:
        entries = controller._custom_queue_entries()
        if not entries:
            return False
        completed_slots = {
            int(value) for value in controller._runtime.get("completed_queue_slots") or []
        }
        return all(int(entry["slot"]) in completed_slots for entry in entries)

    eligible_ids = {
        int(row["id"]) for row in eligible if row.get("id") is not None
    }
    completed_ids = {
        int(value)
        for value in controller._runtime.get("completed_zone_ids_in_window") or []
    }
    return bool(eligible_ids) and eligible_ids.issubset(completed_ids)


async def _confirm_active_completion(self: NavimowerScheduleController) -> bool:
    """Remember when this evaluation completed the final zone of a round."""
    self._defer_continuous_round_handoff = False
    completed = await _ORIGINAL_CONFIRM_ACTIVE_COMPLETION(self)
    if completed and _continuous_round_complete(self):
        self._defer_continuous_round_handoff = True
    return completed


async def _async_send_mow(
    self: NavimowerScheduleController,
    zone_id: int,
    *,
    reset: bool,
    source: str,
    queue_slot: int | None = None,
) -> None:
    """Defer only a cross-round direct handoff while the mower is still moving."""
    defer_round_handoff = bool(
        reset
        and source == "navimower_schedule_next_zone"
        and getattr(self, "_defer_continuous_round_handoff", False)
    )
    self._defer_continuous_round_handoff = False

    activity = (self.coordinator.data or {}).get("activity")
    if defer_round_handoff and activity in {ACTIVITY_MOWING, ACTIVITY_RETURNING}:
        self._runtime["last_command"] = (
            f"round_{int(self._runtime.get('round_index') or 1)}_waiting_idle"
        )
        self._runtime["last_command_at"] = _utc_now()
        self._runtime["last_error"] = None
        await self._save()
        return

    await _ORIGINAL_ASYNC_SEND_MOW(
        self,
        zone_id,
        reset=reset,
        source=source,
        queue_slot=queue_slot,
    )


async def _evaluate_locked(self: NavimowerScheduleController) -> None:
    """Advance a completed Time window round while the same window is still open."""
    await _ORIGINAL_EVALUATE_LOCKED(self)

    if self._mode != SCHEDULE_MODE_WINDOW or not self._enabled:
        return
    in_window, _ = self._window_state(dt_util.now())
    if not in_window or self._runtime.get("suspended_reason"):
        return
    if self._runtime.get("active_zone_id") is not None:
        return
    if isinstance(self._runtime.get("pending_command"), dict):
        return
    if self._runtime.get("resume_pending"):
        return
    if not _continuous_round_complete(self):
        return

    self._runtime["completed_zone_ids_in_window"] = []
    self._runtime["completed_queue_slots"] = []
    self._runtime["active_queue_slot"] = None
    self._runtime["just_completed_zone_id"] = None
    self._runtime["round_index"] = int(self._runtime.get("round_index") or 1) + 1
    self._runtime["round_started_at"] = _utc_now()
    activity = (self.coordinator.data or {}).get("activity")
    self._runtime["last_command"] = (
        f"round_{self._runtime['round_index']}_waiting_idle"
        if activity in {ACTIVITY_MOWING, ACTIVITY_RETURNING}
        else f"round_{self._runtime['round_index']}_ready"
    )
    self._runtime["last_command_at"] = _utc_now()
    self._runtime["last_error"] = None
    await self._save()


def install_schedule_round_semantics() -> None:
    """Install safe repeated-round behavior once."""
    global _INSTALLED
    if _INSTALLED:
        return
    NavimowerScheduleController._confirm_active_completion = _confirm_active_completion
    NavimowerScheduleController._async_send_mow = _async_send_mow
    NavimowerScheduleController._evaluate_locked = _evaluate_locked
    _INSTALLED = True
