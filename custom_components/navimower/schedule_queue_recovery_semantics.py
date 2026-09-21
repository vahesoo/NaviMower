"""Recover a started custom-queue slot after safe same-zone vendor activity.

The queue layer deliberately fails closed when a started slot loses retained-task
ownership.  That prevents a blind reset, but a vendor weather auto-resume can
legitimately continue and complete the same slot after ownership metadata was
lost.  This final scheduler layer restores only evidence that is strong enough
to prove the same queue position:

* the queue is positional/custom and has one earliest started-but-unfinished slot;
* the scheduler is specifically blocked by ``queue_slot_ownership_unverified``;
* the expected zone has fresh vendor-backed activity after the block; and
* either that same zone is mowing now, or it has a confirmed 100% completion
  newer than both the block and the fresh start.

No recovery path issues a reset command.  Once the slot is restored/completed,
the already-composed scheduler decides whether and when the next slot may start.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Awaitable, Callable

from .const import SCHEDULE_ORDER_CUSTOM
from .navimower_schedule import NavimowerScheduleController, _utc_now
from .schedule_logic import later_iso, parse_iso

_INSTALLED = False
_ORIGINAL_EVALUATE_LOCKED: Callable[..., Awaitable[None]] | None = None

_BLOCK_REASON = "queue_slot_ownership_unverified"
# A vendor auto-resume can be visible for several coordinator polls before the
# fail-closed ownership layer records its block. Field evidence showed a 42 s
# gap; 90 s keeps that real handoff recoverable without making old task starts
# eligible.
_ACTIVITY_CLOCK_SKEW_SECONDS = 90.0


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _slot_set(values: Any) -> set[int]:
    result: set[int] = set()
    for raw in values or []:
        value = _as_int(raw)
        if value is not None and value >= 0:
            result.add(value)
    return result


def _execution_queue(controller: NavimowerScheduleController) -> list[int]:
    snapshot = controller._runtime.get("round_queue")
    raw = snapshot if isinstance(snapshot, list) and snapshot else controller._custom_queue
    result: list[int] = []
    for value in raw or []:
        zone_id = _as_int(value)
        if zone_id is not None and zone_id > 0:
            result.append(zone_id)
    return result


def _blocked_unfinished_entry(
    controller: NavimowerScheduleController,
) -> tuple[int, int, dict[str, Any]] | None:
    runtime = controller._runtime
    if not controller._enabled or controller._order_mode != SCHEDULE_ORDER_CUSTOM:
        return None
    if runtime.get("suspended_reason") != _BLOCK_REASON:
        return None
    if runtime.get("active_zone_id") is not None:
        return None
    if isinstance(runtime.get("pending_command"), dict) or runtime.get("resume_pending"):
        return None

    started = _slot_set(runtime.get("started_queue_slots"))
    completed = _slot_set(runtime.get("completed_queue_slots"))
    unfinished = sorted(started - completed)
    if not unfinished:
        return None

    queue = _execution_queue(controller)
    slot = unfinished[0]
    if slot < 0 or slot >= len(queue):
        return None
    zone_id = queue[slot]
    row = controller._zone(zone_id)
    if not isinstance(row, dict):
        return None
    return slot, zone_id, row


def _fresh_start_after_block(
    runtime: dict[str, Any],
    row: dict[str, Any],
) -> str | None:
    blocked = parse_iso(runtime.get("last_command_at"))
    started = parse_iso(row.get("last_started_at"))
    if blocked is None or started is None:
        return None
    if started + timedelta(seconds=_ACTIVITY_CLOCK_SKEW_SECONDS) < blocked:
        return None
    return str(row.get("last_started_at"))


def _observed_vendor_zone(
    controller: NavimowerScheduleController,
    data: dict[str, Any],
) -> int | None:
    if not controller._vendor_mowing(data):
        return None
    for raw in (
        data.get("active_zone_progress_zone_id"),
        (data.get("totals") or {}).get("active_zone_id"),
    ):
        zone_id = _as_int(raw)
        if zone_id is not None:
            return zone_id
    active: list[int] = []
    for row in controller._zones():
        if not row.get("active"):
            continue
        zone_id = _as_int(row.get("id"))
        if zone_id is not None and zone_id not in active:
            active.append(zone_id)
    return active[0] if len(active) == 1 else None


def _confirmed_completion_after_block(
    runtime: dict[str, Any],
    row: dict[str, Any],
) -> str | None:
    started_text = _fresh_start_after_block(runtime, row)
    if started_text is None:
        return None
    started = parse_iso(started_text)
    completed = parse_iso(row.get("last_completed_at"))
    blocked = parse_iso(runtime.get("last_command_at"))
    if started is None or completed is None or blocked is None:
        return None
    if completed <= started or completed <= blocked:
        return None

    coverage = _as_float(row.get("vendor_coverage_pct"))
    if coverage is None:
        coverage = _as_float(row.get("coverage_pct"))
    completed_progress = _as_float(row.get("last_completed_progress"))
    if (coverage is None or coverage < 99.5) and (
        completed_progress is None or completed_progress < 99.5
    ):
        return None

    current_cycle = str(row.get("cycle_id") or "")
    completed_cycle = str(row.get("last_completed_cycle_id") or "")
    if current_cycle and completed_cycle and current_cycle != completed_cycle:
        return None
    if not row.get("last_completed_source") and not row.get("last_completed_confirmation"):
        return None
    return str(row.get("last_completed_at"))


def _adopt_same_zone_resume(
    controller: NavimowerScheduleController,
    *,
    slot: int,
    zone_id: int,
    row: dict[str, Any],
    started_at: str,
) -> None:
    runtime = controller._runtime
    baseline = later_iso(
        row.get("last_completed_at"),
        (runtime.get("scheduler_completed_at") or {}).get(str(zone_id)),
    )
    runtime["active_queue_slot"] = slot
    runtime["active_zone_id"] = zone_id
    runtime["active_cycle_id"] = None
    runtime["active_zone_baseline_completed_at"] = baseline
    runtime["dispatch_started_at"] = started_at
    runtime["just_completed_zone_id"] = None
    runtime["pending_command"] = None
    runtime["retry_not_before"] = None
    runtime["resume_pending"] = False
    runtime["interrupted_reason"] = None
    runtime["interrupted_zone_id"] = None
    runtime["interrupted_cycle_id"] = None
    runtime["progress_before_interrupt"] = None
    runtime["charging_limit_reached_at"] = None
    runtime["suspended_reason"] = None
    runtime["owned_zone_id"] = zone_id
    runtime["owned_dispatch_started_at"] = started_at
    runtime["ownership_source"] = "vendor_same_zone_recovery"
    runtime["last_ownership_result"] = "same_zone_auto_resume_recovered"
    runtime["weather_wait_started_at"] = None
    runtime["weather_clear_seen_at"] = None
    runtime["weather_vendor_code"] = None
    runtime["last_command"] = f"queue_slot_recovered:{slot}:{zone_id}"
    runtime["last_command_at"] = _utc_now()
    runtime["last_error"] = None


def _complete_recovered_slot(
    controller: NavimowerScheduleController,
    *,
    slot: int,
    zone_id: int,
    completed_at: str,
) -> None:
    runtime = controller._runtime
    slots = _slot_set(runtime.get("completed_queue_slots"))
    slots.add(slot)
    runtime["completed_queue_slots"] = sorted(slots)

    completed_zones = {
        value
        for raw in runtime.get("completed_zone_ids_in_window") or []
        if (value := _as_int(raw)) is not None and value > 0
    }
    completed_zones.add(zone_id)
    runtime["completed_zone_ids_in_window"] = sorted(completed_zones)

    confirmed = dict(runtime.get("scheduler_completed_at") or {})
    confirmed[str(zone_id)] = (
        later_iso(confirmed.get(str(zone_id)), completed_at) or completed_at
    )
    runtime["scheduler_completed_at"] = confirmed
    runtime["active_queue_slot"] = None
    runtime["active_zone_id"] = None
    runtime["active_cycle_id"] = None
    runtime["active_zone_baseline_completed_at"] = None
    runtime["dispatch_started_at"] = None
    runtime["just_completed_zone_id"] = zone_id
    runtime["pending_command"] = None
    runtime["retry_not_before"] = None
    runtime["resume_pending"] = False
    runtime["interrupted_reason"] = None
    runtime["interrupted_zone_id"] = None
    runtime["interrupted_cycle_id"] = None
    runtime["progress_before_interrupt"] = None
    runtime["charging_limit_reached_at"] = None
    runtime["suspended_reason"] = None
    runtime["owned_zone_id"] = None
    runtime["owned_dispatch_started_at"] = None
    runtime["ownership_source"] = "vendor_completion_recovery"
    runtime["last_ownership_result"] = "same_zone_completion_recovered"
    runtime["weather_wait_started_at"] = None
    runtime["weather_clear_seen_at"] = None
    runtime["weather_vendor_code"] = None
    runtime["last_command"] = f"queue_slot_completed_recovered:{slot}:{zone_id}"
    runtime["last_command_at"] = _utc_now()
    runtime["last_error"] = None
    controller.coordinator.clear_pending_activity()
    controller.coordinator.clear_command_target()


async def _evaluate_locked(self: NavimowerScheduleController) -> None:
    entry = _blocked_unfinished_entry(self)
    if entry is not None:
        slot, zone_id, row = entry
        completed_at = _confirmed_completion_after_block(self._runtime, row)
        if completed_at is not None:
            _complete_recovered_slot(
                self,
                slot=slot,
                zone_id=zone_id,
                completed_at=completed_at,
            )
            await self._save()
        else:
            started_at = _fresh_start_after_block(self._runtime, row)
            observed_zone = _observed_vendor_zone(self, self.coordinator.data or {})
            if started_at is not None and observed_zone == zone_id:
                _adopt_same_zone_resume(
                    self,
                    slot=slot,
                    zone_id=zone_id,
                    row=row,
                    started_at=started_at,
                )
                await self._save()

    assert _ORIGINAL_EVALUATE_LOCKED is not None
    await _ORIGINAL_EVALUATE_LOCKED(self)


def install_schedule_queue_recovery_semantics() -> None:
    """Install conservative post-weather queue recovery after dispatch arbitration."""
    global _INSTALLED
    global _ORIGINAL_EVALUATE_LOCKED
    if _INSTALLED:
        return
    _ORIGINAL_EVALUATE_LOCKED = NavimowerScheduleController._evaluate_locked
    NavimowerScheduleController._evaluate_locked = _evaluate_locked
    _INSTALLED = True
