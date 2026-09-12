"""Positional custom-queue semantics for Navimower Schedule.

Custom order is a queue of *slots*, not a set of zones. Duplicate zone ids are
intentional and mean that the same zone may run more than once in one round.
A confirmed slot start is therefore reserved for the rest of that round: the
scheduler may resume/continue it, but must never issue another fresh reset for
that slot after ownership context is lost.

The configured queue may be edited at any time. Once a round has started we keep
an immutable runtime snapshot so slot indexes remain stable; editor changes are
picked up by the next round.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Awaitable, Callable

from .const import (
    OPT_SCHEDULE_CUSTOM_QUEUE,
    OPT_SCHEDULE_ORDER_MODE,
    SCHEDULE_ORDER_CUSTOM,
)
from .navimower_schedule import NavimowerScheduleController, _utc_now

_INSTALLED = False
_ORIGINAL_EMPTY_RUNTIME: Callable[[], dict[str, Any]] | None = None
_ORIGINAL_CUSTOM_QUEUE_ENTRIES: Callable[..., list[dict[str, Any]]] | None = None
_ORIGINAL_NEXT_CUSTOM_ENTRY: Callable[..., dict[str, Any] | None] | None = None
_ORIGINAL_CONFIRM_PENDING: Callable[..., Awaitable[None]] | None = None
_ORIGINAL_EVALUATE_LOCKED: Callable[..., Awaitable[None]] | None = None

_OWNERSHIP_BLOCK_REASONS = {
    "active_task_rejected_unverified",
    "retained_task_rejected_unverified",
    "resume_refused_unverified_task",
}


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _slot_set(values: Any) -> set[int]:
    result: set[int] = set()
    for raw in values or []:
        value = _as_int(raw)
        if value is not None and value >= 0:
            result.add(value)
    return result


def _empty_runtime() -> dict[str, Any]:
    assert _ORIGINAL_EMPTY_RUNTIME is not None
    runtime = _ORIGINAL_EMPTY_RUNTIME()
    runtime.update(
        {
            "round_queue": [],
            "started_queue_slots": [],
        }
    )
    return runtime


def _configured_round_queue(controller: NavimowerScheduleController) -> list[int]:
    """Return the currently configured valid queue, preserving duplicates/order."""
    selected = {int(value) for value in controller._selected_zone_ids}
    eligible = {
        int(row["id"])
        for row in controller._eligible_zones()
        if isinstance(row, dict) and row.get("id") is not None
    }
    result: list[int] = []
    for raw in controller._custom_queue:
        zone_id = _as_int(raw)
        if zone_id is not None and zone_id in selected and zone_id in eligible:
            result.append(zone_id)
    return result


def _execution_queue(controller: NavimowerScheduleController) -> list[int]:
    """Return the immutable current-round queue, falling back for old runtime."""
    snapshot = controller._runtime.get("round_queue")
    if isinstance(snapshot, list) and snapshot:
        return [
            value
            for raw in snapshot
            if (value := _as_int(raw)) is not None and value > 0
        ]
    return _configured_round_queue(controller)


def _set_round_snapshot(
    controller: NavimowerScheduleController,
    *,
    queue: list[int] | None = None,
    reset_progress: bool,
) -> None:
    controller._runtime["round_queue"] = list(
        queue if queue is not None else _configured_round_queue(controller)
    )
    if reset_progress:
        controller._runtime["started_queue_slots"] = []
        controller._runtime["completed_queue_slots"] = []
        controller._runtime["active_queue_slot"] = None


def _custom_queue_entries(self: NavimowerScheduleController) -> list[dict[str, Any]]:
    """Build execution entries from the current-round snapshot."""
    eligible = {
        int(row["id"]): row
        for row in self._eligible_zones()
        if isinstance(row, dict) and row.get("id") is not None
    }
    entries: list[dict[str, Any]] = []
    for slot, zone_id in enumerate(_execution_queue(self)):
        if zone_id in eligible and zone_id in self._selected_zone_ids:
            entries.append(
                {"slot": slot, "zone_id": zone_id, "zone": eligible[zone_id]}
            )
    return entries


def _unfinished_started_slots(controller: NavimowerScheduleController) -> set[int]:
    entries = controller._custom_queue_entries()
    valid_slots = {int(entry["slot"]) for entry in entries}
    started = _slot_set(controller._runtime.get("started_queue_slots"))
    completed = _slot_set(controller._runtime.get("completed_queue_slots"))
    return (started - completed) & valid_slots


def _next_custom_entry(self: NavimowerScheduleController) -> dict[str, Any] | None:
    """Choose the next untouched slot; never reset an unfinished started slot."""
    entries = self._custom_queue_entries()
    completed = _slot_set(self._runtime.get("completed_queue_slots"))
    started = _slot_set(self._runtime.get("started_queue_slots"))
    valid_slots = {int(entry["slot"]) for entry in entries}

    # A started-but-not-completed slot owns the queue position. It may be resumed
    # through retained-task logic, but a fresh reset must never skip/restart it.
    if (started - completed) & valid_slots:
        return None

    for entry in entries:
        slot = int(entry["slot"])
        if slot not in completed and slot not in started:
            return entry
    return None


def _matching_custom_queue_slot(
    controller: NavimowerScheduleController,
    zone_id: int,
) -> int | None:
    """Resolve retained work to the exact positional slot, including duplicates."""
    if controller._order_mode != SCHEDULE_ORDER_CUSTOM:
        return None
    entries = controller._custom_queue_entries()
    completed = _slot_set(controller._runtime.get("completed_queue_slots"))
    started = _slot_set(controller._runtime.get("started_queue_slots"))

    active = _as_int(controller._runtime.get("active_queue_slot"))
    if active is not None and active not in completed:
        for entry in entries:
            if int(entry["slot"]) == active and int(entry["zone_id"]) == zone_id:
                return active

    # Prefer the slot that was already confirmed started. This is what makes
    # repeated queues such as [36, 37, 36] unambiguous after a charging pause.
    for entry in entries:
        slot = int(entry["slot"])
        if (
            slot in started
            and slot not in completed
            and int(entry["zone_id"]) == zone_id
        ):
            return slot

    for entry in entries:
        slot = int(entry["slot"])
        if slot not in completed and int(entry["zone_id"]) == zone_id:
            return slot
    return None


def _seed_started_slot_from_runtime(controller: NavimowerScheduleController) -> bool:
    """Migrate an already-running pre-upgrade custom slot without guessing ahead."""
    if controller._order_mode != SCHEDULE_ORDER_CUSTOM:
        return False
    started = _slot_set(controller._runtime.get("started_queue_slots"))
    slot = _as_int(controller._runtime.get("active_queue_slot"))
    if slot is None:
        zone_id = _as_int(controller._runtime.get("active_zone_id"))
        if zone_id is not None:
            slot = _matching_custom_queue_slot(controller, zone_id)
    if slot is None or slot in started:
        return False
    started.add(slot)
    controller._runtime["started_queue_slots"] = sorted(started)
    return True


def _round_has_progress(controller: NavimowerScheduleController) -> bool:
    return bool(
        _slot_set(controller._runtime.get("started_queue_slots"))
        or _slot_set(controller._runtime.get("completed_queue_slots"))
        or controller._runtime.get("active_queue_slot") is not None
        or controller._runtime.get("active_zone_id") is not None
        or isinstance(controller._runtime.get("pending_command"), dict)
        or controller._runtime.get("resume_pending")
    )


async def _async_set_custom_queue(
    self: NavimowerScheduleController,
    zone_ids: list[int],
) -> None:
    """Persist editor order; stage edits for the next round once work has begun."""
    queue = self._normalize_queue(zone_ids)
    selected = set(self._selected_zone_ids)
    if not queue:
        raise ValueError("Custom mowing queue may not be empty")
    unknown = [zone_id for zone_id in queue if zone_id not in selected]
    if unknown:
        raise ValueError(
            f"Queue contains zones outside the selected schedule allowlist: {unknown}"
        )
    eligible = {
        int(row["id"])
        for row in self._eligible_zones()
        if isinstance(row, dict) and row.get("id") is not None
    }
    unproven = [zone_id for zone_id in queue if zone_id not in eligible]
    if unproven:
        raise ValueError(
            f"Queue contains zones without a confirmed completed mowing: {unproven}"
        )

    progress = _round_has_progress(self)
    # Old beta runtime did not have a round snapshot. Capture the old configured
    # order before replacing it so active slot indexes keep their original meaning.
    if progress and not self._runtime.get("round_queue"):
        _set_round_snapshot(self, queue=list(self._custom_queue), reset_progress=False)

    self._custom_queue = list(queue)
    self._order_mode = SCHEDULE_ORDER_CUSTOM
    if not progress:
        _set_round_snapshot(self, queue=list(queue), reset_progress=True)

    self._update_options(
        **{
            OPT_SCHEDULE_CUSTOM_QUEUE: list(queue),
            OPT_SCHEDULE_ORDER_MODE: SCHEDULE_ORDER_CUSTOM,
        }
    )
    self._runtime["last_command"] = (
        "queue_saved_for_next_round" if progress else "queue_saved_for_current_round"
    )
    self._runtime["last_command_at"] = _utc_now()
    self._runtime["last_error"] = None
    await self._save()
    if self._enabled:
        self._queue_evaluation()


async def _confirm_pending(
    self: NavimowerScheduleController,
    data: dict[str, Any],
    activity: Any,
) -> None:
    pending = deepcopy(self._runtime.get("pending_command"))
    assert _ORIGINAL_CONFIRM_PENDING is not None
    await _ORIGINAL_CONFIRM_PENDING(self, data, activity)

    if not isinstance(pending, dict) or pending.get("kind") != "mow":
        return
    slot = _as_int(pending.get("queue_slot"))
    if slot is None:
        return
    if self._runtime.get("pending_command") is not None:
        return
    if _as_int(self._runtime.get("active_queue_slot")) != slot:
        return

    started = _slot_set(self._runtime.get("started_queue_slots"))
    if slot in started:
        return
    started.add(slot)
    self._runtime["started_queue_slots"] = sorted(started)
    await self._save()


async def _evaluate_locked(self: NavimowerScheduleController) -> None:
    """Keep one immutable positional queue for the whole scheduler round."""
    if self._order_mode == SCHEDULE_ORDER_CUSTOM and not self._runtime.get("round_queue"):
        _set_round_snapshot(self, reset_progress=False)
    if _seed_started_slot_from_runtime(self):
        await self._save()

    before_round = _as_int(self._runtime.get("round_index"))
    before_window = self._runtime.get("window_token")

    assert _ORIGINAL_EVALUATE_LOCKED is not None
    await _ORIGINAL_EVALUATE_LOCKED(self)

    after_round = _as_int(self._runtime.get("round_index"))
    after_window = self._runtime.get("window_token")
    boundary_changed = (
        (after_round is not None and before_round is not None and after_round != before_round)
        or (after_window is not None and after_window != before_window)
    )
    if self._order_mode == SCHEDULE_ORDER_CUSTOM and boundary_changed:
        _set_round_snapshot(self, reset_progress=True)
        self._runtime["last_command"] = f"round_queue_snapshot:{after_round or 1}"
        self._runtime["last_command_at"] = _utc_now()
        self._runtime["last_error"] = None
        await self._save()
        return

    # Ownership guards deliberately clear unsafe task context. With a positional
    # queue we must then fail closed: an already-started slot cannot become a new
    # reset command merely because its ownership metadata disappeared.
    if self._enabled and self._order_mode == SCHEDULE_ORDER_CUSTOM:
        unfinished = sorted(_unfinished_started_slots(self))
        if (
            unfinished
            and self._runtime.get("active_zone_id") is None
            and not isinstance(self._runtime.get("pending_command"), dict)
            and not self._runtime.get("resume_pending")
            and self._runtime.get("last_ownership_result") in _OWNERSHIP_BLOCK_REASONS
        ):
            slot = unfinished[0]
            if self._runtime.get("suspended_reason") != "queue_slot_ownership_unverified":
                self._runtime["suspended_reason"] = "queue_slot_ownership_unverified"
                self._runtime["last_command"] = f"queue_slot_blocked:{slot}"
                self._runtime["last_command_at"] = _utc_now()
                self._runtime["last_error"] = (
                    f"Custom queue slot {slot} was already started but its retained "
                    "task ownership could not be proven; a fresh reset was refused"
                )
                await self._save()


def install_schedule_queue_semantics() -> None:
    """Install positional queue protection after pause/ownership/round semantics."""
    global _INSTALLED
    global _ORIGINAL_EMPTY_RUNTIME
    global _ORIGINAL_CUSTOM_QUEUE_ENTRIES
    global _ORIGINAL_NEXT_CUSTOM_ENTRY
    global _ORIGINAL_CONFIRM_PENDING
    global _ORIGINAL_EVALUATE_LOCKED
    if _INSTALLED:
        return

    # Capture at installation time so this layer composes through all previously
    # installed scheduler wrappers instead of bypassing them.
    _ORIGINAL_EMPTY_RUNTIME = NavimowerScheduleController._empty_runtime
    _ORIGINAL_CUSTOM_QUEUE_ENTRIES = NavimowerScheduleController._custom_queue_entries
    _ORIGINAL_NEXT_CUSTOM_ENTRY = NavimowerScheduleController._next_custom_entry
    _ORIGINAL_CONFIRM_PENDING = NavimowerScheduleController._confirm_pending
    _ORIGINAL_EVALUATE_LOCKED = NavimowerScheduleController._evaluate_locked

    NavimowerScheduleController._empty_runtime = staticmethod(_empty_runtime)
    NavimowerScheduleController._custom_queue_entries = _custom_queue_entries
    NavimowerScheduleController._next_custom_entry = _next_custom_entry
    NavimowerScheduleController.async_set_custom_queue = _async_set_custom_queue
    NavimowerScheduleController._confirm_pending = _confirm_pending
    NavimowerScheduleController._evaluate_locked = _evaluate_locked

    # Ownership recovery resolves this helper through the module global at call
    # time, so replacing it here makes duplicate-zone recovery slot-precise too.
    from . import schedule_pause_semantics as pause_semantics

    pause_semantics._matching_custom_queue_slot = _matching_custom_queue_slot
    _INSTALLED = True
