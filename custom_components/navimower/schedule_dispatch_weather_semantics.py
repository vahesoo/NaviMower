"""Reliable dispatch, external-command adoption and weather waits for Schedule.

The managed schedule must distinguish a command it actually sent from a later
manual command, retry a start that was never accepted, and keep an accepted
vendor task parked safely while the mower reports a weather delay.

This module is installed after the existing pause/ownership/round/queue layers,
so it wraps their final composed methods rather than bypassing those semantics.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from homeassistant.util import dt as dt_util

from .navimower_schedule import NavimowerScheduleController, _utc_now
from .schedule_logic import parse_iso

_INSTALLED = False
_ORIGINAL_EMPTY_RUNTIME: Callable[[], dict[str, Any]] | None = None
_ORIGINAL_ASYNC_SEND_MOW: Callable[..., Awaitable[None]] | None = None
_ORIGINAL_CONFIRM_PENDING: Callable[..., Awaitable[None]] | None = None
_ORIGINAL_RECONCILE_UNCONFIRMED: Callable[..., Awaitable[None]] | None = None
_ORIGINAL_EVALUATE_LOCKED: Callable[..., Awaitable[None]] | None = None

_START_RETRY_SECONDS = 60.0
_MAX_START_ATTEMPTS = 3
_ACCEPTED_START_MAX_WAIT_SECONDS = 180.0
_WEATHER_AUTO_RESUME_GRACE_SECONDS = 120.0
_EXTERNAL_TRACE_MAX_AGE_SECONDS = 180.0
_WEATHER_EVENT_MAX_AGE_SECONDS = 600.0
_CROSS_WINDOW_TASK_DELAY_FRESH_SECONDS = 180.0
_WEATHER_CODES = {
    "150A": "rain",
    "150F": "snow",
}
_DIRECT_WEATHER_REASONS = frozenset(
    {"rain", "snow", "wind", "frost", "high_temperature"}
)
_WEATHER_REASONS = frozenset(
    {*_DIRECT_WEATHER_REASONS, "vendor_weather_delay", "vendor_task_delay"}
)


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


def _timestamp(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = None
    if numeric is not None:
        if numeric > 10_000_000_000:
            numeric /= 1000.0
        return numeric if numeric > 0 else None
    parsed = parse_iso(value)
    return parsed.timestamp() if parsed is not None else None


def _age_seconds(value: Any) -> float | None:
    stamp = _timestamp(value)
    if stamp is None:
        return None
    return max(0.0, datetime.now(UTC).timestamp() - stamp)


def _dedupe_ids(values: Any) -> list[int]:
    result: list[int] = []
    for raw in values or []:
        value = _as_int(raw)
        if value is not None and value > 0 and value not in result:
            result.append(value)
    return result


def _empty_runtime() -> dict[str, Any]:
    assert _ORIGINAL_EMPTY_RUNTIME is not None
    runtime = _ORIGINAL_EMPTY_RUNTIME()
    runtime.update(
        {
            "weather_wait_started_at": None,
            "weather_clear_seen_at": None,
            "weather_vendor_code": None,
            "weather_wait_window_token": None,
            "weather_dispatch_hold_reason": None,
            "weather_dispatch_hold_started_at": None,
            "last_external_mow_source": None,
        }
    )
    return runtime


def _mqtt_task_delay(controller: NavimowerScheduleController) -> bool | None:
    center = getattr(controller.coordinator, "notification_center", None)
    getter = getattr(center, "_mqtt_value", None)
    if not callable(getter):
        return None
    try:
        value = getter("task_delay")
    except Exception:
        return None
    if isinstance(value, bool):
        return value
    numeric = _as_int(value)
    if numeric is not None:
        return numeric != 0
    text = str(value or "").strip().lower()
    if text in {"true", "on", "yes"}:
        return True
    if text in {"false", "off", "no", ""}:
        return False
    return None


def _mqtt_task_delay_age(controller: NavimowerScheduleController) -> float | None:
    getter = getattr(controller.coordinator, "mqtt_task_delay_age", None)
    if not callable(getter):
        return None
    try:
        age = getter()
    except Exception:
        return None
    return max(0.0, float(age)) if age is not None else None


def _direct_weather_decision(
    controller: NavimowerScheduleController,
) -> tuple[bool | None, str | None]:
    """Return the fresh vendor weather hold decision, if it is known."""
    data = getattr(controller.coordinator, "data", None) or {}
    if data.get("weather_state_fresh") is not True:
        return None, None
    active = data.get("weather_hold_active")
    if active is True:
        return True, str(data.get("weather_hold_reason") or "vendor_weather_delay")
    if active is False:
        return False, None
    return None, None


def _current_window_token(controller: NavimowerScheduleController) -> str | None:
    try:
        _open, token = controller._window_state(dt_util.now())  # noqa: SLF001
    except Exception:
        return None
    return str(token) if token is not None else None


def _task_delay_crosses_managed_window(
    controller: NavimowerScheduleController,
) -> bool:
    runtime = controller._runtime  # noqa: SLF001
    if not runtime.get("resume_pending"):
        return False
    if str(runtime.get("interrupted_reason") or "") not in _WEATHER_REASONS:
        return False
    current = _current_window_token(controller)
    if current is None:
        return False
    waited = runtime.get("weather_wait_window_token")
    return waited is None or str(waited) != current


def _vendor_message_code(item: dict[str, Any]) -> str | None:
    for key in ("notification_code", "vendor_code", "error_code", "event_code"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip().upper()
    return None


def _vendor_message_timestamp(item: dict[str, Any]) -> float | None:
    for key in ("addtime", "created_at"):
        stamp = _timestamp(item.get(key))
        if stamp is not None:
            return stamp
    return None


def _recent_weather_event(
    controller: NavimowerScheduleController,
    *,
    since: Any,
) -> tuple[str | None, str | None]:
    """Return a recent vendor weather reason/code after the managed dispatch."""
    cache = getattr(controller.coordinator, "_notification_cache", None)
    rows = cache.get("list") if isinstance(cache, dict) else None
    if not isinstance(rows, list):
        return None, None
    since_stamp = _timestamp(since)
    now = datetime.now(UTC).timestamp()
    best: tuple[float, str, str] | None = None
    for item in rows:
        if not isinstance(item, dict):
            continue
        code = _vendor_message_code(item)
        reason = _WEATHER_CODES.get(code or "")
        if reason is None:
            continue
        stamp = _vendor_message_timestamp(item)
        if stamp is None or now - stamp > _WEATHER_EVENT_MAX_AGE_SECONDS:
            continue
        if since_stamp is not None and stamp + 30.0 < since_stamp:
            continue
        if best is None or stamp > best[0]:
            best = (stamp, reason, code or "")
    if best is None:
        return None, None
    return best[1], best[2]


def _weather_delay_reason(
    controller: NavimowerScheduleController,
    *,
    since: Any,
) -> tuple[str | None, str | None]:
    """Prefer the fresh vendor weather decision, then retained-task evidence."""
    direct_active, direct_reason = _direct_weather_decision(controller)
    if direct_active is True:
        return direct_reason or "vendor_weather_delay", None

    delayed = _mqtt_task_delay(controller)
    event_reason, event_code = _recent_weather_event(controller, since=since)
    if delayed is True and _task_delay_crosses_managed_window(controller):
        age = _mqtt_task_delay_age(controller)
        if age is None or age > _CROSS_WINDOW_TASK_DELAY_FRESH_SECONDS:
            delayed = None
    if delayed is True:
        return event_reason or "vendor_task_delay", event_code
    if delayed is False:
        return None, None
    # Some firmwares do not expose taskDelay reliably. A fresh 150A/150F event
    # after this dispatch is still strong enough to hold the managed task.
    return event_reason, event_code


def _last_mow_trace(controller: NavimowerScheduleController) -> dict[str, Any] | None:
    trace = getattr(controller.coordinator, "_last_mow_command_trace", None)
    if not isinstance(trace, dict) or trace.get("send_error"):
        return None
    if (_age_seconds(trace.get("started_at_utc")) or 0.0) > _EXTERNAL_TRACE_MAX_AGE_SECONDS:
        return None
    return trace


def _external_trace_after(
    controller: NavimowerScheduleController,
    *,
    after: Any,
) -> dict[str, Any] | None:
    trace = _last_mow_trace(controller)
    if trace is None:
        return None
    source = str(trace.get("source") or "")
    if not source or source.startswith("navimower_schedule"):
        return None
    trace_stamp = _timestamp(trace.get("started_at_utc"))
    after_stamp = _timestamp(after)
    if trace_stamp is None:
        return None
    if after_stamp is not None and trace_stamp + 0.5 < after_stamp:
        return None
    return trace


def _trace_zone_ids(trace: dict[str, Any] | None) -> list[int]:
    if not isinstance(trace, dict):
        return []
    values = trace.get("resolved_zone_ids")
    if not values:
        values = trace.get("requested_zone_ids")
    return _dedupe_ids(values)


def _started_slots(runtime: dict[str, Any]) -> set[int]:
    result: set[int] = set()
    for raw in runtime.get("started_queue_slots") or []:
        value = _as_int(raw)
        if value is not None and value >= 0:
            result.add(value)
    return result


def _mark_slot_started(runtime: dict[str, Any], slot: Any) -> None:
    value = _as_int(slot)
    if value is None or value < 0:
        return
    started = _started_slots(runtime)
    started.add(value)
    runtime["started_queue_slots"] = sorted(started)


def _zone_cycle_advanced(
    controller: NavimowerScheduleController,
    pending: dict[str, Any],
) -> bool:
    zone_id = _as_int(pending.get("zone_id"))
    row = controller._zone(zone_id) if zone_id is not None else None
    if not isinstance(row, dict):
        return False
    baseline_start = _as_int(pending.get("baseline_vendor_start_time"))
    current_start = _as_int(row.get("vendor_start_time"))
    if baseline_start is not None and current_start is not None and current_start > baseline_start:
        return True
    baseline_cycle = str(pending.get("baseline_cycle_id") or "")
    current_cycle = str(row.get("cycle_id") or "")
    if baseline_cycle and current_cycle and baseline_cycle != current_cycle:
        return True
    baseline_pct = _as_float(pending.get("baseline_coverage_pct"))
    current_pct = _as_float(row.get("coverage_pct"))
    return bool(
        baseline_pct is not None
        and baseline_pct >= 50.0
        and current_pct is not None
        and current_pct <= 5.0
    )


def _tag_scheduler_pending(
    controller: NavimowerScheduleController,
    *,
    zone_id: int,
    attempt: int,
    baseline: dict[str, Any] | None = None,
) -> bool:
    pending = controller._runtime.get("pending_command")
    if not isinstance(pending, dict) or pending.get("kind") != "mow":
        return False
    if not str(pending.get("source") or "").startswith("navimower_schedule"):
        return False
    row = controller._zone(zone_id) or {}
    original = baseline or {}
    pending["attempt"] = max(1, int(attempt))
    pending["baseline_vendor_start_time"] = original.get(
        "baseline_vendor_start_time", row.get("vendor_start_time")
    )
    pending["baseline_coverage_pct"] = original.get(
        "baseline_coverage_pct", row.get("coverage_pct")
    )
    pending["baseline_cycle_id"] = original.get(
        "baseline_cycle_id", row.get("cycle_id")
    )
    return True


async def _async_send_mow(
    self: NavimowerScheduleController,
    zone_id: int,
    *,
    reset: bool,
    source: str,
    queue_slot: int | None = None,
) -> None:
    assert _ORIGINAL_ASYNC_SEND_MOW is not None
    await _ORIGINAL_ASYNC_SEND_MOW(
        self,
        zone_id,
        reset=reset,
        source=source,
        queue_slot=queue_slot,
    )
    if reset and source.startswith("navimower_schedule"):
        if _tag_scheduler_pending(self, zone_id=zone_id, attempt=1):
            await self._save()


def _adopt_pending_weather(
    controller: NavimowerScheduleController,
    pending: dict[str, Any],
    *,
    reason: str,
    vendor_code: str | None,
) -> None:
    runtime = controller._runtime
    zone_id = _as_int(pending.get("zone_id"))
    if zone_id is None:
        return
    sent_at = str(pending.get("sent_at") or _utc_now())
    runtime["active_zone_id"] = zone_id
    runtime["active_queue_slot"] = pending.get("queue_slot")
    runtime["active_cycle_id"] = None
    runtime["active_zone_baseline_completed_at"] = pending.get("baseline_completed_at")
    runtime["dispatch_started_at"] = sent_at
    runtime["just_completed_zone_id"] = None
    runtime["resume_pending"] = True
    runtime["interrupted_reason"] = reason
    runtime["interrupted_zone_id"] = zone_id
    runtime["interrupted_cycle_id"] = None
    runtime["progress_before_interrupt"] = controller._progress_for_zone(zone_id)
    runtime["charging_limit_reached_at"] = None
    runtime["pending_command"] = None
    runtime["retry_not_before"] = None
    runtime["suspended_reason"] = None
    runtime["owned_zone_id"] = zone_id
    runtime["owned_dispatch_started_at"] = sent_at
    runtime["ownership_source"] = str(
        pending.get("source") or "navimower_schedule_next_zone"
    )
    runtime["last_ownership_result"] = "scheduler_start_delayed_by_weather"
    runtime["weather_wait_started_at"] = _utc_now()
    runtime["weather_wait_window_token"] = _current_window_token(controller)
    runtime["weather_clear_seen_at"] = None
    runtime["weather_vendor_code"] = vendor_code
    runtime["last_command"] = f"weather_wait:{reason}:{zone_id}"
    runtime["last_command_at"] = _utc_now()
    runtime["last_error"] = None
    _mark_slot_started(runtime, pending.get("queue_slot"))
    controller.coordinator.clear_pending_activity()
    controller.coordinator.clear_command_target()


async def _adopt_external_same_zone(
    controller: NavimowerScheduleController,
    pending: dict[str, Any],
    trace: dict[str, Any],
) -> None:
    runtime = controller._runtime
    zone_id = _as_int(pending.get("zone_id"))
    if zone_id is None:
        return
    started_at = str(trace.get("started_at_utc") or pending.get("sent_at") or _utc_now())
    runtime["active_zone_id"] = zone_id
    runtime["active_queue_slot"] = pending.get("queue_slot")
    runtime["active_cycle_id"] = None
    runtime["active_zone_baseline_completed_at"] = pending.get("baseline_completed_at")
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
    runtime["ownership_source"] = str(trace.get("source") or "external_same_zone")
    runtime["last_ownership_result"] = "external_same_zone_adopted"
    runtime["last_external_mow_source"] = str(trace.get("source") or "external_same_zone")
    runtime["last_command"] = f"external_same_zone_adopted:{zone_id}"
    runtime["last_command_at"] = _utc_now()
    runtime["last_error"] = None
    _mark_slot_started(runtime, pending.get("queue_slot"))
    await controller._save()


async def _confirm_pending(
    self: NavimowerScheduleController,
    data: dict[str, Any],
    activity: Any,
) -> None:
    pending = deepcopy(self._runtime.get("pending_command"))
    if isinstance(pending, dict) and pending.get("kind") == "mow":
        zone_id = _as_int(pending.get("zone_id"))
        trace = _external_trace_after(self, after=pending.get("sent_at"))
        if (
            zone_id is not None
            and _trace_zone_ids(trace) == [zone_id]
            and self._vendor_mowing(data)
        ):
            await _adopt_external_same_zone(self, pending, trace or {})
            return
    assert _ORIGINAL_CONFIRM_PENDING is not None
    await _ORIGINAL_CONFIRM_PENDING(self, data, activity)


async def _reconcile_unconfirmed_mow_start(
    self: NavimowerScheduleController,
) -> None:
    pending = deepcopy(self._runtime.get("pending_command"))
    if not isinstance(pending, dict) or pending.get("kind") != "mow":
        assert _ORIGINAL_RECONCILE_UNCONFIRMED is not None
        await _ORIGINAL_RECONCILE_UNCONFIRMED(self)
        return
    source = str(pending.get("source") or "")
    if not source.startswith("navimower_schedule"):
        assert _ORIGINAL_RECONCILE_UNCONFIRMED is not None
        await _ORIGINAL_RECONCILE_UNCONFIRMED(self)
        return

    since = pending.get("sent_at")
    reason, vendor_code = _weather_delay_reason(self, since=since)
    if reason is not None:
        _adopt_pending_weather(self, pending, reason=reason, vendor_code=vendor_code)
        await self._save()
        return

    age = _age_seconds(since)
    if age is None or age < _START_RETRY_SECONDS:
        return
    data = self.coordinator.data or {}
    if self._vendor_mowing(data):
        return

    attempt = max(1, _as_int(pending.get("attempt")) or 1)
    if _zone_cycle_advanced(self, pending):
        if age < _ACCEPTED_START_MAX_WAIT_SECONDS:
            self._runtime["last_command"] = f"mow_start_accepted_waiting_motion:{pending.get('zone_id')}"
            self._runtime["last_command_at"] = _utc_now()
            self._runtime["last_error"] = None
            await self._save()
            return
        self.coordinator.clear_pending_activity()
        self.coordinator.clear_command_target()
        self._runtime["pending_command"] = None
        self._runtime["suspended_reason"] = "mow_start_accepted_but_not_running"
        self._runtime["last_error"] = (
            "The vendor cycle changed but mowing never became active; automatic reset retry was refused"
        )
        self._runtime["last_command"] = f"mow_start_accepted_not_running:{pending.get('zone_id')}"
        self._runtime["last_command_at"] = _utc_now()
        await self._save()
        return

    if attempt >= _MAX_START_ATTEMPTS:
        self.coordinator.clear_pending_activity()
        self.coordinator.clear_command_target()
        self._runtime["pending_command"] = None
        self._runtime["suspended_reason"] = "mow_start_not_confirmed"
        self._runtime["last_error"] = (
            f"New-zone mowing start was not confirmed after {attempt} managed attempts"
        )
        self._runtime["last_command"] = f"mow_start_unconfirmed:{pending.get('zone_id')}"
        self._runtime["last_command_at"] = _utc_now()
        await self._save()
        return

    zone_id = _as_int(pending.get("zone_id"))
    if zone_id is None:
        return
    queue_slot = _as_int(pending.get("queue_slot"))
    baseline = {
        "baseline_vendor_start_time": pending.get("baseline_vendor_start_time"),
        "baseline_coverage_pct": pending.get("baseline_coverage_pct"),
        "baseline_cycle_id": pending.get("baseline_cycle_id"),
    }
    self.coordinator.clear_pending_activity()
    self.coordinator.clear_command_target()
    self._runtime["pending_command"] = None
    assert _ORIGINAL_ASYNC_SEND_MOW is not None
    await _ORIGINAL_ASYNC_SEND_MOW(
        self,
        zone_id,
        reset=True,
        source=source,
        queue_slot=queue_slot,
    )
    if _tag_scheduler_pending(
        self,
        zone_id=zone_id,
        attempt=attempt + 1,
        baseline=baseline,
    ):
        self._runtime["last_command"] = f"mow_retry:{zone_id}:attempt={attempt + 1}"
        self._runtime["last_command_at"] = _utc_now()
        self._runtime["last_error"] = None
        await self._save()


def _set_weather_interruption(
    controller: NavimowerScheduleController,
    *,
    reason: str,
    vendor_code: str | None,
) -> bool:
    zone_id = _as_int(controller._runtime.get("active_zone_id"))
    if zone_id is None:
        return False
    runtime = controller._runtime
    changed = False
    current_reason = str(runtime.get("interrupted_reason") or "")
    if (
        runtime.get("resume_pending")
        and current_reason
        and current_reason not in _WEATHER_REASONS
    ):
        # Weather can overlap low-battery/window/manual interruption. Keep the
        # stronger existing ownership reason and simply hold evaluation until
        # the fresh weather decision clears.
        controller.coordinator.clear_pending_activity()
        controller.coordinator.clear_command_target()
        return False
    if not runtime.get("resume_pending") or current_reason not in _WEATHER_REASONS:
        runtime["resume_pending"] = True
        runtime["interrupted_reason"] = reason
        runtime["interrupted_zone_id"] = zone_id
        runtime["interrupted_cycle_id"] = runtime.get("active_cycle_id")
        runtime["progress_before_interrupt"] = controller._progress_for_zone(zone_id)
        runtime["charging_limit_reached_at"] = None
        runtime["pending_command"] = None
        runtime["weather_wait_started_at"] = _utc_now()
        runtime["weather_wait_window_token"] = _current_window_token(controller)
        runtime["weather_clear_seen_at"] = None
        runtime["last_command"] = f"weather_wait:{reason}:{zone_id}"
        runtime["last_command_at"] = _utc_now()
        runtime["last_error"] = None
        changed = True
    if vendor_code and runtime.get("weather_vendor_code") != vendor_code:
        runtime["weather_vendor_code"] = vendor_code
        changed = True
    controller.coordinator.clear_pending_activity()
    controller.coordinator.clear_command_target()
    return changed


def _external_override_trace(
    controller: NavimowerScheduleController,
) -> dict[str, Any] | None:
    runtime = controller._runtime
    reference = None
    pending = runtime.get("pending_command")
    if isinstance(pending, dict):
        reference = pending.get("sent_at")
    if reference is None:
        reference = runtime.get("dispatch_started_at") or runtime.get("owned_dispatch_started_at")
    if reference is None:
        return None
    return _external_trace_after(controller, after=reference)


async def _evaluate_locked(self: NavimowerScheduleController) -> None:
    runtime = self._runtime

    # A fresh vendor weather decision is also a pre-dispatch guard. This is the
    # missing case where the mower is physically Docked but the Navimow app says
    # Rain/Snow/Wind/Frost/High temperature delay: do not send a mowing command.
    direct_active, direct_reason = _direct_weather_decision(self)
    dispatch_hold = runtime.get("weather_dispatch_hold_reason")
    no_owned_task = (
        runtime.get("active_zone_id") is None
        and not runtime.get("resume_pending")
        and not isinstance(runtime.get("pending_command"), dict)
    )
    if (
        direct_active is True
        and self._window_open_now()
        and no_owned_task
        and not runtime.get("suspended_reason")
    ):
        reason = direct_reason or "vendor_weather_delay"
        if dispatch_hold != reason:
            runtime["weather_dispatch_hold_reason"] = reason
            runtime["weather_dispatch_hold_started_at"] = (
                runtime.get("weather_dispatch_hold_started_at") or _utc_now()
            )
            runtime["last_command"] = f"weather_dispatch_hold:{reason}"
            runtime["last_command_at"] = _utc_now()
            runtime["last_error"] = None
            await self._save()
        return

    if dispatch_hold is not None:
        if direct_active is None:
            # A hold must be released only by a fresh explicit clear decision.
            return
        if direct_active is True:
            return
        runtime["weather_dispatch_hold_reason"] = None
        runtime["weather_dispatch_hold_started_at"] = None
        runtime["last_command"] = f"weather_dispatch_clear:{dispatch_hold}"
        runtime["last_command_at"] = _utc_now()
        runtime["last_error"] = None
        await self._save()

    # A later explicit HA mowing command must never be relabelled as the managed
    # scheduler's own acknowledgement. Same-zone work may be adopted so the queue
    # can continue; different-zone work pauses managed dispatch fail-closed.
    external = _external_override_trace(self)
    expected_zone = _as_int(
        (runtime.get("pending_command") or {}).get("zone_id")
        if isinstance(runtime.get("pending_command"), dict)
        else runtime.get("active_zone_id")
    )
    external_ids = _trace_zone_ids(external)
    if external is not None and expected_zone is not None and external_ids:
        if external_ids != [expected_zone]:
            runtime["pending_command"] = None
            runtime["suspended_reason"] = "external_override"
            runtime["last_external_mow_source"] = str(external.get("source") or "external")
            runtime["last_ownership_result"] = "external_other_zone_override"
            runtime["last_command"] = "schedule_suspended_external_override"
            runtime["last_command_at"] = _utc_now()
            runtime["last_error"] = None
            self.coordinator.clear_pending_activity()
            self.coordinator.clear_command_target()
            await self._save()
            return

    pending = runtime.get("pending_command")
    if isinstance(pending, dict) and pending.get("kind") == "mow":
        reason, vendor_code = _weather_delay_reason(self, since=pending.get("sent_at"))
        if reason is not None:
            _adopt_pending_weather(self, deepcopy(pending), reason=reason, vendor_code=vendor_code)
            await self._save()
            return

    zone_id = _as_int(runtime.get("active_zone_id"))
    if zone_id is not None and not self._vendor_mowing(self.coordinator.data or {}):
        since = runtime.get("dispatch_started_at") or runtime.get("weather_wait_started_at")
        reason, vendor_code = _weather_delay_reason(self, since=since)
        if reason is not None:
            if _set_weather_interruption(self, reason=reason, vendor_code=vendor_code):
                await self._save()
            return

    interrupted = str(runtime.get("interrupted_reason") or "")
    if runtime.get("resume_pending") and interrupted in _WEATHER_REASONS:
        data = self.coordinator.data or {}
        if self._vendor_mowing(data):
            runtime["weather_wait_started_at"] = None
            runtime["weather_wait_window_token"] = None
            runtime["weather_clear_seen_at"] = None
            runtime["weather_vendor_code"] = None
            # Let the composed original path clear resume/interruption ownership.
        else:
            reason, vendor_code = _weather_delay_reason(
                self,
                since=runtime.get("weather_wait_started_at") or runtime.get("dispatch_started_at"),
            )
            if reason is not None:
                runtime["weather_vendor_code"] = vendor_code or runtime.get("weather_vendor_code")
                return
            if not self._window_open_now():
                return
            clear_seen = runtime.get("weather_clear_seen_at")
            if clear_seen is None:
                runtime["weather_clear_seen_at"] = _utc_now()
                runtime["last_command"] = f"weather_cleared_waiting_vendor_resume:{zone_id}"
                runtime["last_command_at"] = _utc_now()
                await self._save()
                return
            clear_age = _age_seconds(clear_seen)
            if clear_age is None or clear_age < _WEATHER_AUTO_RESUME_GRACE_SECONDS:
                return
            await self._continue_interrupted_task(
                source="navimower_schedule_weather_resume",
                continue_source="navimower_schedule_weather_continue_fallback",
            )
            return

    assert _ORIGINAL_EVALUATE_LOCKED is not None
    await _ORIGINAL_EVALUATE_LOCKED(self)


def install_schedule_dispatch_weather_semantics() -> None:
    """Install final scheduler command/weather arbitration after queue semantics."""
    global _INSTALLED
    global _ORIGINAL_EMPTY_RUNTIME
    global _ORIGINAL_ASYNC_SEND_MOW
    global _ORIGINAL_CONFIRM_PENDING
    global _ORIGINAL_RECONCILE_UNCONFIRMED
    global _ORIGINAL_EVALUATE_LOCKED
    if _INSTALLED:
        return

    # Capture the fully composed scheduler at install time. runtime.py imports all
    # modules before installing them, so import-time capture would be too early.
    _ORIGINAL_EMPTY_RUNTIME = NavimowerScheduleController._empty_runtime
    _ORIGINAL_ASYNC_SEND_MOW = NavimowerScheduleController._async_send_mow
    _ORIGINAL_CONFIRM_PENDING = NavimowerScheduleController._confirm_pending
    _ORIGINAL_RECONCILE_UNCONFIRMED = NavimowerScheduleController._reconcile_unconfirmed_mow_start
    _ORIGINAL_EVALUATE_LOCKED = NavimowerScheduleController._evaluate_locked

    NavimowerScheduleController._empty_runtime = staticmethod(_empty_runtime)
    NavimowerScheduleController._async_send_mow = _async_send_mow
    NavimowerScheduleController._confirm_pending = _confirm_pending
    NavimowerScheduleController._reconcile_unconfirmed_mow_start = _reconcile_unconfirmed_mow_start
    NavimowerScheduleController._evaluate_locked = _evaluate_locked
    _INSTALLED = True
