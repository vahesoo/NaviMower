"""Scheduler V2: a small time-window + queue-pointer state machine.

The managed schedule does not own vendor tasks. It remembers only which queue
slot is unfinished, observes vendor completion, and decides whether commands are
allowed by the configured mowing window.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    ACTIVITY_DOCKED,
    ACTIVITY_MOWING,
    ACTIVITY_PAUSED,
    ACTIVITY_RETURNING,
    OPT_SCHEDULE_CUSTOM_QUEUE,
    OPT_SCHEDULE_ORDER_MODE,
    SCHEDULE_ORDER_CUSTOM,
)
from .navimower_schedule import (
    NavimowerScheduleController,
    _age_seconds,
    _as_int,
    _utc_now,
)
from .schedule_logic import completion_advanced, parse_iso

_INSTALLED = False
_ORIGINAL_ASYNC_START = NavimowerScheduleController.async_start

_RESUME_CONFIRM_SECONDS = 45.0
_CONTINUE_CONFIRM_SECONDS = 90.0
_START_CONFIRM_SECONDS = 120.0
_COMMAND_RETRY_SECONDS = 60.0
_DOCK_RETRY_SECONDS = 60.0

_OWNERSHIP_SUSPENSIONS = {
    "queue_slot_ownership_unverified",
    "mow_start_not_confirmed",
    "mow_start_zone_mismatch",
    "interrupted_task_continue_not_confirmed",
    "interrupted_task_continue_failed",
}


def _empty_runtime() -> dict[str, Any]:
    """Return the single Scheduler V2 persisted state."""
    return {
        "scheduler_schema_version": 2,
        "v2_initialized": False,
        "window_token": None,
        "last_window_open": None,
        "round_index": 1,
        "round_started_at": None,
        "round_queue": [],
        "current_slot": None,
        "active_queue_slot": None,
        "active_zone_id": None,
        "active_zone_baseline_completed_at": None,
        "dispatch_started_at": None,
        "unfinished": False,
        "interrupted_reason": None,
        "pending_command": None,
        "retry_not_before": None,
        "resume_attempted_window_token": None,
        "resume_attempted_at": None,
        "continue_attempted_at": None,
        "window_close_dock_probe_at": None,
        "window_close_dock_probe_result": None,
        "outside_window_dock_at": None,
        "scheduler_completed_at": {},
        "last_command": None,
        "last_command_at": None,
        "last_error": None,
        "suspended_reason": None,
        "migration_source": None,
        # Read-only beta7 migration inputs. V2 never uses these as state.
        "completed_queue_slots": [],
        "started_queue_slots": [],
        "completed_zone_ids_in_window": [],
        "just_completed_zone_id": None,
        "resume_pending": False,
        "interrupted_zone_id": None,
        "interrupted_cycle_id": None,
        "progress_before_interrupt": None,
        "charging_limit_reached_at": None,
        "active_cycle_id": None,
    }


def _stamp(value: Any) -> datetime | None:
    parsed = parse_iso(value)
    return parsed.astimezone(UTC) if parsed is not None else None


def _retry_ready(runtime: dict[str, Any]) -> bool:
    stamp = _stamp(runtime.get("retry_not_before"))
    return stamp is None or datetime.now(UTC) >= stamp


def _set_retry(runtime: dict[str, Any], seconds: float = _COMMAND_RETRY_SECONDS) -> None:
    runtime["retry_not_before"] = (
        datetime.now(UTC) + timedelta(seconds=max(1.0, seconds))
    ).isoformat()


def _zone_completed_after(
    controller: NavimowerScheduleController,
    zone_id: int,
    baseline: Any,
    dispatched_at: Any,
) -> bool:
    row = controller._zone(zone_id)
    if not isinstance(row, dict):
        return False
    return completion_advanced(
        row.get("last_completed_at"),
        baseline,
        dispatched_at,
    )


def _build_round_queue(controller: NavimowerScheduleController) -> list[int]:
    """Snapshot the configured queue for one round."""
    eligible = [
        row
        for row in controller._eligible_zones()
        if isinstance(row, dict) and _as_int(row.get("id"))
    ]
    allowed = {
        int(row["id"]): row
        for row in eligible
        if _as_int(row.get("id")) is not None
    }
    if controller._order_mode == SCHEDULE_ORDER_CUSTOM:
        return [
            int(zone_id)
            for zone_id in controller._custom_queue
            if int(zone_id) in allowed
        ]

    def key(row: dict[str, Any]) -> tuple[float, int]:
        completed = _stamp(row.get("last_completed_at"))
        value = completed.timestamp() if completed is not None else 0.0
        return (value, int(row["id"]))

    return [int(row["id"]) for row in sorted(eligible, key=key)]


def _start_round(
    controller: NavimowerScheduleController,
    *,
    increment: bool,
    reason: str,
) -> None:
    runtime = controller._runtime
    if increment:
        runtime["round_index"] = int(runtime.get("round_index") or 1) + 1
    else:
        runtime["round_index"] = max(1, int(runtime.get("round_index") or 1))
    runtime["round_queue"] = _build_round_queue(controller)
    runtime["current_slot"] = 0 if runtime["round_queue"] else None
    runtime["active_queue_slot"] = runtime["current_slot"]
    runtime["active_zone_id"] = (
        runtime["round_queue"][0] if runtime["round_queue"] else None
    )
    runtime["active_zone_baseline_completed_at"] = None
    runtime["dispatch_started_at"] = None
    runtime["unfinished"] = False
    runtime["interrupted_reason"] = None
    runtime["pending_command"] = None
    runtime["retry_not_before"] = None
    runtime["resume_attempted_window_token"] = None
    runtime["resume_attempted_at"] = None
    runtime["continue_attempted_at"] = None
    runtime["round_started_at"] = _utc_now()
    runtime["last_command"] = f"round_ready:{runtime['round_index']}:{reason}"
    runtime["last_command_at"] = _utc_now()
    runtime["last_error"] = None
    runtime["suspended_reason"] = None


def _migrate_legacy_runtime(controller: NavimowerScheduleController) -> bool:
    """Collapse the beta7 slot/ownership state into the V2 queue pointer."""
    runtime = controller._runtime
    if runtime.get("v2_initialized") is True:
        return False

    queue = [
        int(value)
        for value in runtime.get("round_queue") or []
        if _as_int(value) is not None and int(value) > 0
    ]
    if not queue:
        queue = _build_round_queue(controller)
    runtime["round_queue"] = queue

    completed = {
        int(value)
        for value in runtime.get("completed_queue_slots") or []
        if _as_int(value) is not None and 0 <= int(value) < len(queue)
    }
    started = {
        int(value)
        for value in runtime.get("started_queue_slots") or []
        if _as_int(value) is not None and 0 <= int(value) < len(queue)
    }
    round_started = _stamp(runtime.get("round_started_at"))
    confirmed = dict(runtime.get("scheduler_completed_at") or {})

    # Recover field completions that ownership guards failed to book into slots.
    for slot in sorted(started - completed):
        zone_id = queue[slot]
        row = controller._zone(zone_id) or {}
        current = _stamp(row.get("last_completed_at"))
        previous = _stamp(confirmed.get(str(zone_id)))
        if current is None:
            continue
        if round_started is not None and current >= round_started:
            if previous is None or current > previous:
                completed.add(slot)
                confirmed[str(zone_id)] = current.isoformat()

    runtime["scheduler_completed_at"] = confirmed
    unfinished_slots = [slot for slot in range(len(queue)) if slot not in completed]
    if not unfinished_slots and queue:
        _start_round(controller, increment=True, reason="beta7_migration_complete")
    elif unfinished_slots:
        current = next(
            (slot for slot in unfinished_slots if slot in started),
            unfinished_slots[0],
        )
        zone_id = queue[current]
        runtime["current_slot"] = current
        runtime["active_queue_slot"] = current
        runtime["active_zone_id"] = zone_id
        runtime["unfinished"] = current in started
        row = controller._zone(zone_id) or {}
        if runtime["unfinished"]:
            runtime["active_zone_baseline_completed_at"] = (
                runtime.get("active_zone_baseline_completed_at")
                or confirmed.get(str(zone_id))
                or row.get("last_completed_at")
            )
            runtime["dispatch_started_at"] = (
                runtime.get("dispatch_started_at")
                or runtime.get("round_started_at")
                or _utc_now()
            )
        else:
            runtime["active_zone_baseline_completed_at"] = None
            runtime["dispatch_started_at"] = None
    else:
        _start_round(controller, increment=False, reason="beta7_migration_empty")

    if runtime.get("suspended_reason") in _OWNERSHIP_SUSPENSIONS:
        runtime["suspended_reason"] = None
    runtime["pending_command"] = None
    runtime["resume_pending"] = False
    runtime["retry_not_before"] = None
    runtime["completed_queue_slots"] = []
    runtime["started_queue_slots"] = []
    runtime["completed_zone_ids_in_window"] = []
    runtime["just_completed_zone_id"] = None
    runtime["v2_initialized"] = True
    runtime["scheduler_schema_version"] = 2
    runtime["migration_source"] = "beta7_queue_state"
    runtime["last_error"] = None
    return True


def _current_slot(
    controller: NavimowerScheduleController,
) -> tuple[int, int] | None:
    runtime = controller._runtime
    queue = runtime.get("round_queue") or []
    slot = _as_int(runtime.get("current_slot"))
    if slot is None or slot < 0 or slot >= len(queue):
        return None
    zone_id = _as_int(queue[slot])
    if zone_id is None or zone_id <= 0:
        return None
    runtime["active_queue_slot"] = slot
    runtime["active_zone_id"] = zone_id
    return slot, zone_id


def _weather_hold(controller: NavimowerScheduleController) -> str | None:
    data = controller.coordinator.data or {}
    if (
        data.get("weather_state_fresh") is True
        and data.get("weather_hold_active") is True
    ):
        return str(data.get("weather_hold_reason") or "weather")
    return None


def _night_hold(controller: NavimowerScheduleController) -> bool:
    data = controller.coordinator.data or {}
    settings = data.get("settings") or {}
    if settings.get("night_mow") is not False:
        return False
    center = getattr(controller.coordinator, "notification_center", None)
    return getattr(center, "interrupted_reason", None) == "night"


def _charging_ready(controller: NavimowerScheduleController, data: dict[str, Any]) -> bool:
    if not controller._vendor_charging(data):
        return True
    battery = _as_int(data.get("battery"))
    limit = controller._charging_limit_percent(data)
    return bool(
        battery is not None
        and limit is not None
        and battery >= limit
    )


async def _send_resume(controller: NavimowerScheduleController, zone_id: int) -> None:
    runtime = controller._runtime
    sent_at = _utc_now()
    try:
        await controller.coordinator.async_send(
            controller.coordinator.client.resume,
            controller.coordinator.sn,
        )
    except Exception as err:
        runtime["last_error"] = f"Resume failed: {type(err).__name__}: {err}"
        runtime["resume_attempted_window_token"] = runtime.get("window_token")
        runtime["resume_attempted_at"] = sent_at
        runtime["pending_command"] = None
        _set_retry(runtime)
        await controller._save()
        return

    runtime["resume_attempted_window_token"] = runtime.get("window_token")
    runtime["resume_attempted_at"] = sent_at
    runtime["pending_command"] = {
        "kind": "resume",
        "zone_id": zone_id,
        "sent_at": sent_at,
        "source": "navimower_schedule_v2_resume",
    }
    runtime["last_command"] = f"resume:{zone_id}"
    runtime["last_command_at"] = sent_at
    runtime["last_error"] = None
    await controller._save()


async def _send_new_slot(
    controller: NavimowerScheduleController,
    slot: int,
    zone_id: int,
) -> None:
    runtime = controller._runtime
    row = controller._zone(zone_id) or {}
    runtime["current_slot"] = slot
    runtime["active_queue_slot"] = slot
    runtime["active_zone_id"] = zone_id
    runtime["active_zone_baseline_completed_at"] = row.get("last_completed_at")
    runtime["dispatch_started_at"] = _utc_now()
    runtime["unfinished"] = True
    runtime["interrupted_reason"] = None
    runtime["resume_attempted_window_token"] = None
    runtime["resume_attempted_at"] = None
    runtime["continue_attempted_at"] = None
    await controller._async_send_mow(
        zone_id,
        reset=True,
        source="navimower_schedule_v2_next_zone",
        queue_slot=slot,
    )
    if not isinstance(runtime.get("pending_command"), dict):
        # The command failed before it reached the mower. Retry as a fresh start,
        # not as retained work.
        runtime["unfinished"] = False
        runtime["active_zone_baseline_completed_at"] = None
        runtime["dispatch_started_at"] = None
        _set_retry(runtime)
        await controller._save()


async def _send_continue(
    controller: NavimowerScheduleController,
    slot: int,
    zone_id: int,
) -> None:
    runtime = controller._runtime
    await controller._async_send_mow(
        zone_id,
        reset=False,
        source="navimower_schedule_v2_continue",
        queue_slot=slot,
    )
    runtime["resume_pending"] = False
    runtime["continue_attempted_at"] = _utc_now()
    if not isinstance(runtime.get("pending_command"), dict):
        if runtime.get("suspended_reason") == "interrupted_task_continue_failed":
            runtime["suspended_reason"] = None
        _set_retry(runtime)
        await controller._save()
        return
    runtime["suspended_reason"] = None
    runtime["last_command"] = f"continue:{zone_id}"
    runtime["last_command_at"] = _utc_now()
    await controller._save()


async def _send_dock(
    controller: NavimowerScheduleController,
    *,
    reason: str,
    force: bool,
) -> None:
    runtime = controller._runtime
    previous = runtime.get("outside_window_dock_at")
    if not force:
        age = _age_seconds(previous)
        if age is not None and age < _DOCK_RETRY_SECONDS:
            return
    await controller._async_send_dock(f"navimower_schedule_v2_{reason}")
    runtime["outside_window_dock_at"] = _utc_now()
    if reason == "window_closed_probe":
        runtime["window_close_dock_probe_at"] = runtime["outside_window_dock_at"]
        runtime["window_close_dock_probe_result"] = (
            "sent" if isinstance(runtime.get("pending_command"), dict) else "send_failed"
        )
    await controller._save()


async def _confirm_pending(
    controller: NavimowerScheduleController,
    data: dict[str, Any],
    activity: Any,
) -> None:
    runtime = controller._runtime
    pending = runtime.get("pending_command")
    if not isinstance(pending, dict):
        return
    kind = str(pending.get("kind") or "")
    if kind == "dock":
        if activity in {ACTIVITY_RETURNING, ACTIVITY_DOCKED}:
            runtime["pending_command"] = None
            await controller._save()
        return

    if controller._vendor_mowing(data):
        if kind == "mow":
            zone_id = _as_int(pending.get("zone_id"))
            if zone_id is not None:
                controller.coordinator.start_new_mowing_cycle(
                    [zone_id],
                    source=str(
                        pending.get("source")
                        or "navimower_schedule_v2_next_zone"
                    ),
                )
                runtime["active_zone_id"] = zone_id
                runtime["active_queue_slot"] = pending.get("queue_slot")
                runtime["unfinished"] = True
        runtime["pending_command"] = None
        # The legacy base command helper still writes this compatibility flag for
        # reset=False. Scheduler V2 does not use it as state.
        runtime["resume_pending"] = False
        runtime["interrupted_reason"] = None
        runtime["retry_not_before"] = None
        await controller._save()
        return

    age = _age_seconds(pending.get("sent_at"))
    if age is None:
        return
    if kind == "resume" and age >= _RESUME_CONFIRM_SECONDS:
        runtime["pending_command"] = None
        await controller._save()
        return
    if kind == "continue" and age >= _CONTINUE_CONFIRM_SECONDS:
        runtime["pending_command"] = None
        _set_retry(runtime)
        await controller._save()
        return
    if kind == "mow" and age >= _START_CONFIRM_SECONDS:
        # Do not permanently suspend. If the vendor created a new cycle/progress
        # we keep the unfinished slot; otherwise allow a fresh retry.
        zone_id = _as_int(pending.get("zone_id"))
        row = controller._zone(zone_id) if zone_id is not None else None
        baseline = _stamp(pending.get("baseline_completed_at"))
        current = _stamp((row or {}).get("last_completed_at"))
        runtime["pending_command"] = None
        if current is None or baseline is None or current <= baseline:
            runtime["unfinished"] = False
            runtime["active_zone_baseline_completed_at"] = None
            runtime["dispatch_started_at"] = None
        _set_retry(runtime)
        await controller._save()


def _complete_current_slot(controller: NavimowerScheduleController) -> bool:
    runtime = controller._runtime
    current = _current_slot(controller)
    if current is None or not runtime.get("unfinished"):
        return False
    slot, zone_id = current
    if not _zone_completed_after(
        controller,
        zone_id,
        runtime.get("active_zone_baseline_completed_at"),
        runtime.get("dispatch_started_at"),
    ):
        return False

    row = controller._zone(zone_id) or {}
    stamp = row.get("last_completed_at") or _utc_now()
    confirmed = dict(runtime.get("scheduler_completed_at") or {})
    confirmed[str(zone_id)] = str(stamp)
    runtime["scheduler_completed_at"] = confirmed
    runtime["last_command"] = f"zone_completed:{zone_id}:slot={slot}"
    runtime["last_command_at"] = _utc_now()
    runtime["last_error"] = None
    runtime["pending_command"] = None
    runtime["unfinished"] = False
    runtime["interrupted_reason"] = None
    runtime["active_zone_baseline_completed_at"] = None
    runtime["dispatch_started_at"] = None
    runtime["resume_attempted_window_token"] = None
    runtime["resume_attempted_at"] = None
    runtime["continue_attempted_at"] = None

    next_slot = slot + 1
    queue = runtime.get("round_queue") or []
    if next_slot >= len(queue):
        _start_round(controller, increment=True, reason="round_complete")
    else:
        runtime["current_slot"] = next_slot
        runtime["active_queue_slot"] = next_slot
        runtime["active_zone_id"] = int(queue[next_slot])
    return True


async def _enforce_closed_window(
    controller: NavimowerScheduleController,
    data: dict[str, Any],
    activity: Any,
    *,
    just_closed: bool,
) -> None:
    runtime = controller._runtime
    if runtime.get("unfinished"):
        runtime["interrupted_reason"] = "window_closed"

    pending = runtime.get("pending_command")
    if isinstance(pending, dict) and pending.get("kind") in {"mow", "resume", "continue"}:
        runtime["pending_command"] = None

    # At the boundary send one Dock even while charging/docked. This is the
    # deliberate retained-task cancellation probe requested by the V2 policy.
    if just_closed and runtime.get("unfinished"):
        await _send_dock(
            controller,
            reason="window_closed_probe",
            force=True,
        )
        return

    # Any actual mowing/paused attempt outside the window is sent home. This
    # catches sunrise auto-resume and post-charge auto-resume.
    if controller._vendor_mowing(data) or activity == ACTIVITY_PAUSED:
        await _send_dock(
            controller,
            reason="outside_window",
            force=False,
        )
        return
    await controller._save()


async def _evaluate_locked(controller: NavimowerScheduleController) -> None:
    if not controller._enabled:
        return

    data = controller.coordinator.data or {}
    controller._maybe_migrate_legacy_zone_selection()
    settings = data.get("settings") or {}
    if settings.get("schedule_enabled") is True:
        await controller.async_set_enabled(False, reason="native_schedule_enabled")
        return

    changed = _migrate_legacy_runtime(controller)
    if not controller._runtime.get("round_queue"):
        _start_round(controller, increment=False, reason="initial")
        changed = True

    runtime = controller._runtime
    now = dt_util.now()
    in_window, token = controller._window_state(now)
    previous_window = runtime.get("last_window_open")
    just_opened = previous_window is False and in_window
    just_closed = previous_window is True and not in_window
    runtime["last_window_open"] = bool(in_window)

    if in_window and token:
        token_text = str(token)
        if token_text != runtime.get("window_token"):
            runtime["window_token"] = token_text
            runtime["resume_attempted_window_token"] = None
            runtime["resume_attempted_at"] = None
            runtime["continue_attempted_at"] = None
            runtime["outside_window_dock_at"] = None
            changed = True

    completed_now = _complete_current_slot(controller)
    if completed_now:
        changed = True

    activity = data.get("activity")
    await _confirm_pending(controller, data, activity)

    if changed:
        await controller._save()

    if not in_window:
        await _enforce_closed_window(
            controller,
            data,
            activity,
            just_closed=just_closed,
        )
        return

    if data.get("error") is True or data.get("problem_latched") is True:
        runtime["suspended_reason"] = "mower_problem"
        await controller._save()
        return
    if runtime.get("suspended_reason") == "mower_problem":
        runtime["suspended_reason"] = None

    weather = _weather_hold(controller)
    if weather:
        runtime["interrupted_reason"] = weather
        runtime["suspended_reason"] = None
        await controller._save()
        return

    if _night_hold(controller):
        runtime["interrupted_reason"] = "night"
        runtime["suspended_reason"] = None
        if activity == ACTIVITY_PAUSED:
            await _send_dock(controller, reason="night_hold", force=False)
        else:
            await controller._save()
        return

    current = _current_slot(controller)
    if current is None:
        _start_round(controller, increment=True, reason="queue_repaired")
        current = _current_slot(controller)
        if current is None:
            await controller._save()
            return
    slot, zone_id = current

    # A completion can arrive in the same coordinator snapshot that still says
    # Mowing for the just-finished zone. Do not adopt that stale activity as the
    # newly advanced slot; dispatch the next slot/round below instead.
    if controller._vendor_mowing(data) and not completed_now:
        runtime["unfinished"] = True
        runtime["interrupted_reason"] = None
        runtime["pending_command"] = None
        await controller._save()
        return

    if not _retry_ready(runtime):
        return

    if runtime.get("unfinished"):
        # If charging is still below the user's configured threshold, let the
        # mower charge. At/above the threshold V2 may resume even if the vendor
        # still reports Charging.
        if not _charging_ready(controller, data):
            return

        pending = runtime.get("pending_command")
        if isinstance(pending, dict):
            return

        if runtime.get("resume_attempted_window_token") != runtime.get("window_token"):
            await _send_resume(controller, zone_id)
            return

        # Resume was already tried for this window. Use an exact-zone continue
        # instead of another provenance/ownership decision.
        await _send_continue(controller, slot, zone_id)
        return

    # A fresh queue slot may hand off immediately from the previous completed
    # zone/round. No Dock boundary is required.
    if controller._vendor_charging(data) and not _charging_ready(controller, data):
        return
    if activity not in {
        ACTIVITY_DOCKED,
        ACTIVITY_PAUSED,
        ACTIVITY_RETURNING,
        ACTIVITY_MOWING,
        None,
    } and not just_opened:
        return

    await _send_new_slot(controller, slot, zone_id)


async def _async_start(controller: NavimowerScheduleController) -> None:
    await _ORIGINAL_ASYNC_START(controller)
    # Preserve the existing explicit reset service without installing any of the
    # old ownership/pause wrapper chain.
    from .schedule_pause_semantics import _register_reset_service

    _register_reset_service(controller.hass)


async def _async_reset_schedule(
    controller: NavimowerScheduleController,
    *,
    reason: str,
) -> None:
    data = controller.coordinator.data or {}
    if controller._vendor_mowing(data) or data.get("activity") == ACTIVITY_RETURNING:
        raise RuntimeError(
            "Reset schedule is refused while the mower is mowing or returning; dock it first"
        )
    async with controller._lock:
        confirmed = deepcopy(controller._runtime.get("scheduler_completed_at") or {})
        controller._runtime = _empty_runtime()
        controller._runtime["scheduler_completed_at"] = confirmed
        controller._runtime["v2_initialized"] = True
        controller._runtime["migration_source"] = "explicit_reset"
        _start_round(controller, increment=False, reason=reason)
        await controller._save()
    if controller._enabled:
        controller._queue_evaluation()


async def _async_set_custom_queue(
    controller: NavimowerScheduleController,
    zone_ids: list[int],
) -> None:
    """Stage a new custom queue; apply it at the next round boundary."""
    queue = controller._normalize_queue(zone_ids)
    selected = set(controller._selected_zone_ids)
    if not queue:
        raise ValueError("Custom mowing queue may not be empty")
    unknown = [zone_id for zone_id in queue if zone_id not in selected]
    if unknown:
        raise ValueError(
            f"Queue contains zones outside the selected schedule allowlist: {unknown}"
        )
    eligible = {
        int(row["id"])
        for row in controller._eligible_zones()
        if row.get("id") is not None
    }
    unproven = [zone_id for zone_id in queue if zone_id not in eligible]
    if unproven:
        raise ValueError(
            f"Queue contains zones without a confirmed completed mowing: {unproven}"
        )

    controller._custom_queue = queue
    controller._order_mode = SCHEDULE_ORDER_CUSTOM
    controller._update_options(
        **{
            OPT_SCHEDULE_CUSTOM_QUEUE: list(queue),
            OPT_SCHEDULE_ORDER_MODE: SCHEDULE_ORDER_CUSTOM,
        }
    )
    controller._runtime["last_command"] = "queue_staged_for_next_round"
    controller._runtime["last_command_at"] = _utc_now()
    await controller._save()
    if controller._enabled:
        controller._queue_evaluation()


def install_schedule_v2_semantics() -> None:
    """Install one scheduler policy layer instead of the legacy wrapper chain."""
    global _INSTALLED
    if _INSTALLED:
        return
    NavimowerScheduleController._empty_runtime = staticmethod(_empty_runtime)
    NavimowerScheduleController.async_start = _async_start
    NavimowerScheduleController.async_reset_schedule = _async_reset_schedule
    NavimowerScheduleController.async_set_custom_queue = _async_set_custom_queue
    NavimowerScheduleController._evaluate_locked = _evaluate_locked
    _INSTALLED = True
