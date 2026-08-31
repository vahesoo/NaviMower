"""Strict ownership guards for Navimower Schedule retained tasks.

Navimower Schedule owns one mower zone at a time. A generic vendor Resume command,
however, resumes the mower's whole retained task and does not accept a zone id.
That makes stale scheduler runtime dangerous: an old ``active_zone_id`` must never
be enough to adopt or resume a native/manual multi-zone task.

This extension records explicit ownership after a scheduler start is confirmed,
rejects conflicting live task evidence, and fails closed when upgrading older
runtime that cannot prove scheduler ownership.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import schedule_pause_semantics as pause_semantics
from .navimower_schedule import NavimowerScheduleController, _utc_now

_INSTALLED = False
_ORIGINAL_EMPTY_RUNTIME = NavimowerScheduleController._empty_runtime
_ORIGINAL_CONFIRM_PENDING = NavimowerScheduleController._confirm_pending
_ORIGINAL_CONFIRM_ACTIVE_COMPLETION = NavimowerScheduleController._confirm_active_completion
_ORIGINAL_CONTINUE_INTERRUPTED_TASK = NavimowerScheduleController._continue_interrupted_task
_ORIGINAL_EVALUATE_LOCKED = NavimowerScheduleController._evaluate_locked
_ORIGINAL_ADOPT_RETAINED_TASK = pause_semantics._adopt_retained_task


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _dedupe_ids(values: Any) -> list[int]:
    result: list[int] = []
    for raw in values or []:
        value = _as_int(raw)
        if value is not None and value > 0 and value not in result:
            result.append(value)
    return result


def _empty_runtime() -> dict[str, Any]:
    runtime = _ORIGINAL_EMPTY_RUNTIME()
    runtime.update(
        {
            "owned_zone_id": None,
            "owned_dispatch_started_at": None,
            "ownership_source": None,
            "last_ownership_result": None,
        }
    )
    return runtime


def _notification_task(controller: NavimowerScheduleController) -> dict[str, Any] | None:
    center = getattr(controller.coordinator, "notification_center", None)
    task = getattr(center, "active_task", None)
    return task if isinstance(task, dict) else None


def _scheduler_task_evidence(task: dict[str, Any] | None, zone_id: int) -> bool:
    """Return positive persisted evidence that a one-zone task came from scheduler."""
    if task is None:
        return False
    trigger = str(task.get("trigger") or "")
    task_ids = _dedupe_ids(task.get("zone_ids"))
    return trigger.startswith("navimower_schedule") and task_ids == [zone_id]


def _live_task_conflicts(controller: NavimowerScheduleController, zone_id: int) -> bool:
    """Reject clear evidence that the mower is currently cutting another task."""
    data = controller.coordinator.data or {}
    if not controller._vendor_mowing(data):
        return False

    # While mowing, the coordinator's resolved target reflects the vendor task
    # once a short-lived HA command intent has expired. A scheduler-owned task is
    # strictly one-zone-at-a-time; a different/multi-zone live target conflicts.
    target_ids = _dedupe_ids(data.get("target_zone_ids"))
    target_source = str(data.get("target_zone_source") or "")
    if target_ids and target_source not in {"last_known", "none"} and target_ids != [zone_id]:
        return True

    task = _notification_task(controller)
    if task is None:
        return False
    trigger = str(task.get("trigger") or "")
    task_ids = _dedupe_ids(task.get("zone_ids"))
    # Scheduler handoffs may leave Notification Center on the previous scheduler
    # zone because mowing never transitioned through idle. That is still scheduler
    # provenance. Native/manual/other-HA task context is a real conflict.
    if task_ids and not trigger.startswith("navimower_schedule"):
        return True
    return False


def _ownership_proven(controller: NavimowerScheduleController, zone_id: int) -> bool:
    if zone_id <= 0 or zone_id not in controller._selected_zone_ids:
        return False
    if _live_task_conflicts(controller, zone_id):
        return False

    owned = _as_int(controller._runtime.get("owned_zone_id"))
    if owned == zone_id:
        return True

    task = _notification_task(controller)
    if _scheduler_task_evidence(task, zone_id):
        # Safe beta9 -> beta10 migration: only a persisted one-zone task with an
        # explicit scheduler trigger may seed the new ownership fields.
        controller._runtime["owned_zone_id"] = zone_id
        controller._runtime["owned_dispatch_started_at"] = (
            controller._runtime.get("dispatch_started_at")
            or task.get("started_at")
            or _utc_now()
        )
        controller._runtime["ownership_source"] = "migrated_scheduler_task_context"
        controller._runtime["last_ownership_result"] = "ownership_migrated"
        return True
    return False


def _clear_unverified_context(controller: NavimowerScheduleController, *, reason: str) -> None:
    """Drop scheduler ownership only; never send a mower command here."""
    controller._runtime["active_zone_id"] = None
    controller._runtime["active_queue_slot"] = None
    controller._runtime["active_cycle_id"] = None
    controller._runtime["active_zone_baseline_completed_at"] = None
    controller._runtime["dispatch_started_at"] = None
    controller._runtime["resume_pending"] = False
    controller._runtime["interrupted_reason"] = None
    controller._runtime["interrupted_zone_id"] = None
    controller._runtime["interrupted_cycle_id"] = None
    controller._runtime["progress_before_interrupt"] = None
    controller._runtime["charging_limit_reached_at"] = None
    controller._runtime["pending_command"] = None
    controller._runtime["retry_not_before"] = None
    controller._runtime["owned_zone_id"] = None
    controller._runtime["owned_dispatch_started_at"] = None
    controller._runtime["ownership_source"] = None
    controller._runtime["last_ownership_result"] = reason
    controller.coordinator.clear_pending_activity()
    controller.coordinator.clear_command_target()


def _adopt_retained_task(controller: NavimowerScheduleController) -> int | None:
    """Adopt only a task whose one-zone scheduler ownership is positively proven."""
    active_zone_id = _as_int(controller._runtime.get("active_zone_id"))
    if active_zone_id is not None:
        if _ownership_proven(controller, active_zone_id):
            controller._runtime["last_ownership_result"] = "retained_task_verified"
            return _ORIGINAL_ADOPT_RETAINED_TASK(controller)
        _clear_unverified_context(controller, reason="retained_task_rejected_unverified")
        return None

    task = _notification_task(controller)
    task_ids = _dedupe_ids(task.get("zone_ids")) if task is not None else []
    if len(task_ids) == 1 and _scheduler_task_evidence(task, task_ids[0]):
        adopted = _ORIGINAL_ADOPT_RETAINED_TASK(controller)
        if adopted is not None:
            controller._runtime["owned_zone_id"] = int(adopted)
            controller._runtime["owned_dispatch_started_at"] = (
                controller._runtime.get("dispatch_started_at") or _utc_now()
            )
            controller._runtime["ownership_source"] = "scheduler_task_context"
            controller._runtime["last_ownership_result"] = "retained_task_verified"
        return adopted
    return None


async def _confirm_pending(
    self: NavimowerScheduleController,
    data: dict[str, Any],
    activity: Any,
) -> None:
    pending = deepcopy(self._runtime.get("pending_command"))
    await _ORIGINAL_CONFIRM_PENDING(self, data, activity)
    if not isinstance(pending, dict) or pending.get("kind") != "mow":
        return
    zone_id = _as_int(pending.get("zone_id"))
    if zone_id is None or _as_int(self._runtime.get("active_zone_id")) != zone_id:
        return
    if isinstance(self._runtime.get("pending_command"), dict):
        return
    source = str(pending.get("source") or "")
    if not source.startswith("navimower_schedule"):
        return
    self._runtime["owned_zone_id"] = zone_id
    self._runtime["owned_dispatch_started_at"] = (
        pending.get("sent_at") or self._runtime.get("dispatch_started_at") or _utc_now()
    )
    self._runtime["ownership_source"] = source
    self._runtime["last_ownership_result"] = "scheduler_start_confirmed"
    await self._save()


async def _confirm_active_completion(self: NavimowerScheduleController) -> bool:
    completed = await _ORIGINAL_CONFIRM_ACTIVE_COMPLETION(self)
    if completed:
        self._runtime["owned_zone_id"] = None
        self._runtime["owned_dispatch_started_at"] = None
        self._runtime["ownership_source"] = None
        self._runtime["last_ownership_result"] = "owned_zone_completed"
        await self._save()
    return completed


async def _continue_interrupted_task(
    self: NavimowerScheduleController,
    *,
    source: str,
    continue_source: str,
) -> None:
    zone_id = _as_int(
        self._runtime.get("interrupted_zone_id") or self._runtime.get("active_zone_id")
    )
    if zone_id is None or not _ownership_proven(self, zone_id):
        _clear_unverified_context(self, reason="resume_refused_unverified_task")
        self._runtime["last_command"] = "resume_refused_unverified_task"
        self._runtime["last_command_at"] = _utc_now()
        self._runtime["last_error"] = (
            "Retained mower task ownership could not be proven; vendor Resume was refused"
        )
        await self._save()
        return
    self._runtime["last_ownership_result"] = "resume_ownership_verified"
    await _ORIGINAL_CONTINUE_INTERRUPTED_TASK(
        self,
        source=source,
        continue_source=continue_source,
    )


async def _evaluate_locked(self: NavimowerScheduleController) -> None:
    """Fail closed on stale pre-beta10 ownership before normal scheduler decisions."""
    zone_id = _as_int(self._runtime.get("active_zone_id"))
    if zone_id is not None and not isinstance(self._runtime.get("pending_command"), dict):
        if not _ownership_proven(self, zone_id):
            _clear_unverified_context(self, reason="active_task_rejected_unverified")
            self._runtime["last_command"] = "stale_scheduler_ownership_cleared"
            self._runtime["last_command_at"] = _utc_now()
            self._runtime["last_error"] = None
            await self._save()
    await _ORIGINAL_EVALUATE_LOCKED(self)


def install_schedule_ownership_semantics() -> None:
    """Install strict retained-task ownership once, after pause semantics."""
    global _INSTALLED
    if _INSTALLED:
        return
    NavimowerScheduleController._empty_runtime = staticmethod(_empty_runtime)
    NavimowerScheduleController._confirm_pending = _confirm_pending
    NavimowerScheduleController._confirm_active_completion = _confirm_active_completion
    NavimowerScheduleController._continue_interrupted_task = _continue_interrupted_task
    NavimowerScheduleController._evaluate_locked = _evaluate_locked
    # pause_semantics._async_set_enabled resolves this module global at call time.
    pause_semantics._adopt_retained_task = _adopt_retained_task
    _INSTALLED = True
