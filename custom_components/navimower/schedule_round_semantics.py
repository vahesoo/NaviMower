"""Safe continuous-round rollover semantics for Navimower Schedule.

Direct handoff is useful between zones inside one scheduler round: after a zone
completes the mower can be redirected while it is already returning instead of
visiting the dock first. The final zone of a 24-hour round is different. At that
boundary the base scheduler clears the completed set and advances ``round_index``
before selecting the first zone of the next round. Sending that new ``reset=true``
command while the mower still reports mowing/returning can race the vendor's
normal task-finish transition and leave the new start unconfirmed.

This extension keeps direct handoff inside a round, but defers only the first
command of a newly-advanced continuous round until a later evaluation sees the
mower in an ordinary start state (Docked/Paused). Charging and other existing
safety gates remain owned by the base scheduler.
"""
from __future__ import annotations

from .const import (
    ACTIVITY_MOWING,
    ACTIVITY_RETURNING,
    SCHEDULE_MODE_CONTINUOUS,
    SCHEDULE_ORDER_CUSTOM,
)
from .navimower_schedule import NavimowerScheduleController, _utc_now

_INSTALLED = False
_ORIGINAL_CONFIRM_ACTIVE_COMPLETION = NavimowerScheduleController._confirm_active_completion
_ORIGINAL_ASYNC_SEND_MOW = NavimowerScheduleController._async_send_mow


def _continuous_round_complete(controller: NavimowerScheduleController) -> bool:
    """Return whether the just-confirmed completion finished the current round."""
    if controller._mode != SCHEDULE_MODE_CONTINUOUS:
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
    """Remember when this evaluation completed the final zone of a 24-hour round."""
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


def install_schedule_round_semantics() -> None:
    """Install safe continuous-round rollover behavior once."""
    global _INSTALLED
    if _INSTALLED:
        return
    NavimowerScheduleController._confirm_active_completion = _confirm_active_completion
    NavimowerScheduleController._async_send_mow = _async_send_mow
    _INSTALLED = True
