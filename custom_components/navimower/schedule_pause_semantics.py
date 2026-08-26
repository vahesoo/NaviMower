"""Pause/resume semantics for the integration-owned Navimower schedule.

The Schedule switch is a scheduler pause control, not a destructive cycle reset.
Turning it off preserves runtime ownership, queue position and retained-task
metadata. Turning it back on resumes the same task when it can be identified
safely. A separate ``navimower.reset_schedule`` action is the only user-facing
way to clear the current scheduler round/queue runtime deliberately.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    ACTIVITY_RETURNING,
    DOMAIN,
    OPT_SCHEDULE_ENABLED,
    SCHEDULE_ORDER_CUSTOM,
)
from .navimower_schedule import NavimowerScheduleController, _utc_now

SERVICE_RESET_SCHEDULE = "reset_schedule"
RESET_SCHEDULE_SCHEMA = vol.Schema({vol.Optional("device_id"): cv.string})

_INSTALLED = False
_ORIGINAL_ASYNC_START = NavimowerScheduleController.async_start


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _notification_context(controller: NavimowerScheduleController) -> tuple[dict[str, Any] | None, str | None]:
    center = getattr(controller.coordinator, "notification_center", None)
    task = getattr(center, "active_task", None)
    reason = getattr(center, "interrupted_reason", None)
    return (task if isinstance(task, dict) else None, str(reason) if reason else None)


def _matching_custom_queue_slot(controller: NavimowerScheduleController, zone_id: int) -> int | None:
    if controller._order_mode != SCHEDULE_ORDER_CUSTOM:
        return None
    completed = {int(value) for value in controller._runtime.get("completed_queue_slots") or []}
    for entry in controller._custom_queue_entries():
        slot = int(entry["slot"])
        if slot not in completed and int(entry["zone_id"]) == zone_id:
            return slot
    return None


def _adopt_retained_task(controller: NavimowerScheduleController) -> int | None:
    """Attach one safely-identifiable unfinished mower task to scheduler runtime."""
    data = controller.coordinator.data or {}
    vendor_mowing = controller._vendor_mowing(data)
    task, reason = _notification_context(controller)
    normalized_reason = "low_battery" if reason == "charging" else reason

    active_zone_id = _as_int(controller._runtime.get("active_zone_id"))
    if active_zone_id is not None:
        if vendor_mowing or controller._runtime.get("resume_pending"):
            return active_zone_id
        if normalized_reason:
            controller._runtime["resume_pending"] = True
            controller._runtime["interrupted_reason"] = normalized_reason
            controller._runtime["interrupted_zone_id"] = active_zone_id
            controller._runtime["interrupted_cycle_id"] = controller._runtime.get("active_cycle_id")
            controller._runtime["progress_before_interrupt"] = controller._progress_for_zone(active_zone_id)
            controller._runtime["charging_limit_reached_at"] = None
            controller._runtime["pending_command"] = None
            return active_zone_id
        return None

    if task is None:
        return None
    raw_zone_ids = [_as_int(value) for value in task.get("zone_ids") or []]
    task_zone_ids = [value for value in raw_zone_ids if value is not None and value > 0]
    allowed = [value for value in task_zone_ids if value in controller._selected_zone_ids]
    if not allowed:
        return None

    observed_zone = _as_int(data.get("active_zone_progress_zone_id"))
    if observed_zone in allowed:
        zone_id = observed_zone
    elif len(set(allowed)) == 1:
        zone_id = allowed[0]
    else:
        # One-zone-at-a-time ownership must not guess inside an ambiguous
        # retained multi-zone task.
        return None

    # A currently mowing task is observable proof. A stopped task needs a
    # retained interruption reason from Notification Center before takeover.
    if not vendor_mowing and not normalized_reason:
        return None

    row = controller._zone(zone_id) or {}
    controller._runtime["active_zone_id"] = zone_id
    controller._runtime["active_queue_slot"] = _matching_custom_queue_slot(controller, zone_id)
    controller._runtime["active_cycle_id"] = None
    controller._runtime["active_zone_baseline_completed_at"] = row.get("last_completed_at")
    controller._runtime["dispatch_started_at"] = task.get("started_at") or _utc_now()
    controller._runtime["just_completed_zone_id"] = None
    controller._runtime["pending_command"] = None
    controller._runtime["retry_not_before"] = None
    controller._runtime["charging_limit_reached_at"] = None
    if vendor_mowing:
        controller._runtime["resume_pending"] = False
        controller._runtime["interrupted_reason"] = None
        controller._runtime["interrupted_zone_id"] = None
        controller._runtime["interrupted_cycle_id"] = None
        controller._runtime["progress_before_interrupt"] = None
    else:
        controller._runtime["resume_pending"] = True
        controller._runtime["interrupted_reason"] = normalized_reason
        controller._runtime["interrupted_zone_id"] = zone_id
        controller._runtime["interrupted_cycle_id"] = None
        controller._runtime["progress_before_interrupt"] = controller._progress_for_zone(zone_id)
    return zone_id


async def _async_set_enabled(
    self: NavimowerScheduleController,
    enabled: bool,
    *,
    reason: str,
) -> None:
    """Pause/resume the scheduler without destroying its runtime state."""
    enabled = bool(enabled)
    if enabled == self._enabled:
        return

    adopted_zone: int | None = None
    if enabled:
        if not self._eligible_zones():
            raise RuntimeError(
                "Configure at least one successfully completed automatic mowing zone first"
            )
        native = (self.coordinator.data or {}).get("settings", {}).get("schedule_enabled")
        if native is None:
            raise RuntimeError("Native mowing schedule state is not available yet")
        if native is True:
            await self._async_set_native_schedule(False)

        self._enabled = True
        if self._runtime.get("suspended_reason") == "schedule_paused":
            self._runtime["suspended_reason"] = None
        adopted_zone = _adopt_retained_task(self)
        self._runtime["last_command"] = (
            f"resumed_adopted:{adopted_zone}:{reason}"
            if adopted_zone is not None
            else f"resumed:{reason}"
        )
    else:
        self._enabled = False
        # Preserve any stronger safety/error suspension. Otherwise expose the
        # switch state as a reversible scheduler pause in diagnostics.
        if self._runtime.get("suspended_reason") is None:
            self._runtime["suspended_reason"] = "schedule_paused"
        self._runtime["last_command"] = f"paused:{reason}"

    self._runtime["last_command_at"] = _utc_now()
    self._legacy_selection_migration_allowed = False
    self._update_options(**{OPT_SCHEDULE_ENABLED: enabled})
    await self._save()
    if enabled:
        await self.async_evaluate()


async def _async_reset_schedule(
    self: NavimowerScheduleController,
    *,
    reason: str,
) -> None:
    """Clear the current scheduler round deliberately without sending a mower command."""
    data = self.coordinator.data or {}
    if self._vendor_mowing(data) or data.get("activity") == ACTIVITY_RETURNING:
        raise RuntimeError(
            "Reset schedule is refused while the mower is mowing or returning; pause/dock it first"
        )

    async with self._lock:
        confirmed = deepcopy(self._runtime.get("scheduler_completed_at") or {})
        self._runtime = self._empty_runtime()
        self._runtime["scheduler_completed_at"] = confirmed
        if not self._enabled:
            self._runtime["suspended_reason"] = "schedule_paused"
        self._runtime["last_command"] = f"reset_schedule:{reason}"
        self._runtime["last_command_at"] = _utc_now()
        self._runtime["last_error"] = None
        await self._save()

    if self._enabled:
        self._queue_evaluation()


def _resolve_controller(hass: HomeAssistant, call: ServiceCall) -> NavimowerScheduleController:
    store = hass.data.get(DOMAIN) or {}
    coordinators = [
        value
        for key, value in store.items()
        if not str(key).startswith("_")
        and hasattr(value, "entry")
        and getattr(value, "navimower_schedule", None) is not None
    ]
    device_id = call.data.get("device_id")
    if device_id:
        device = dr.async_get(hass).async_get(device_id)
        if device:
            for entry_id in device.config_entries:
                coordinator = store.get(entry_id)
                controller = getattr(coordinator, "navimower_schedule", None)
                if controller is not None:
                    return controller
        raise ServiceValidationError("device_id is not a Navimower mower")
    if len(coordinators) == 1:
        return coordinators[0].navimower_schedule
    raise ServiceValidationError(
        "Multiple Navimow mowers configured: pass device_id to choose one"
    )


def _register_reset_service(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_RESET_SCHEDULE):
        return

    async def _reset_schedule(call: ServiceCall) -> None:
        controller = _resolve_controller(hass, call)
        try:
            await controller.async_reset_schedule(reason="home_assistant_action")
        except RuntimeError as err:
            raise ServiceValidationError(str(err)) from err
        except Exception as err:
            raise HomeAssistantError(f"Navimower reset_schedule failed: {err}") from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_SCHEDULE,
        _reset_schedule,
        schema=RESET_SCHEDULE_SCHEMA,
    )


async def _async_start(self: NavimowerScheduleController) -> None:
    await _ORIGINAL_ASYNC_START(self)
    _register_reset_service(self.hass)


def install_schedule_pause_semantics() -> None:
    """Install pause/resume ownership and explicit reset behavior once."""
    global _INSTALLED
    if _INSTALLED:
        return
    NavimowerScheduleController.async_start = _async_start
    NavimowerScheduleController.async_set_enabled = _async_set_enabled
    NavimowerScheduleController.async_reset_schedule = _async_reset_schedule
    _INSTALLED = True
