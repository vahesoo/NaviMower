"""Services for Navimower."""
from __future__ import annotations

import asyncio
import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .runtime import install_runtime_extensions
from .const import (
    ACTIVITY_MOWING,
    ACTIVITY_RETURNING,
    DOMAIN,
    OPT_CHANNELS,
    encode_partition_ids,
    mow_setup,
)
from .gate_area_editor import delete_gate_area, upsert_gate_area
from .georeference_tools import async_relearn_georeference
from .map_snapshot import active_map_snapshot_managers
from .model_support import supports_ordered_zone_mowing
from .notification_actions import (
    async_mark_all_notifications_read,
    async_mark_notification_read,
)
from .resume import async_resume_task
from .task_resume import (
    RESUME_STRATEGY_ORDERED_RUN,
    RESUME_STRATEGY_VENDOR,
    guard_managed_schedule_resume,
    task_resume_decision,
    paused_mow_request_decision,
    confirmed_paused_vendor_task,
    ordered_run_matches_current_task,
)

install_runtime_extensions()

SERVICE_SET_SCHEDULE = "set_schedule"
SERVICE_MOW = "mow"
SERVICE_CONTINUE_LAST_ORDERED_RUN = "continue_last_ordered_run"
SERVICE_CONTINUE_TASK = "continue_task"
SERVICE_RESUME = "resume"
SERVICE_SET_SCHEDULE_QUEUE = "set_schedule_queue"
SERVICE_SET_GATE_AREA = "set_gate_area"
SERVICE_DELETE_GATE_AREA = "delete_gate_area"
SERVICE_MARK_NOTIFICATION_READ = "mark_notification_read"
SERVICE_MARK_ALL_NOTIFICATIONS_READ = "mark_all_notifications_read"
SERVICE_RELEARN_GEOREFERENCE = "relearn_georeference"
SERVICE_REFRESH_MAP_SNAPSHOT = "refresh_map_snapshot"

_WEEKDAY_TO_NUM = {
    "sunday": 1,
    "monday": 2,
    "tuesday": 3,
    "wednesday": 4,
    "thursday": 5,
    "friday": 6,
    "saturday": 7,
}

_PERIOD_SCHEMA = vol.Schema(
    {
        vol.Required("start"): cv.string,
        vol.Required("end"): cv.string,
        vol.Optional("zones", default=list): vol.All(cv.ensure_list, [vol.Coerce(int)]),
    }
)

SET_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("day"): vol.In(list(_WEEKDAY_TO_NUM)),
        vol.Optional("enabled", default=True): cv.boolean,
        vol.Optional("periods", default=list): vol.All(cv.ensure_list, [_PERIOD_SCHEMA]),
    }
)

MOW_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Optional("zones", default=list): vol.All(cv.ensure_list, [vol.Coerce(int)]),
        vol.Optional("reset", default=True): cv.boolean,
    }
)

SET_SCHEDULE_QUEUE_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("zones"): vol.All(cv.ensure_list, [vol.Coerce(int)]),
    }
)

SET_GATE_AREA_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Optional("gate_area_id"): vol.All(cv.string, vol.Length(max=128)),
        vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=64)),
        vol.Required("polygon"): cv.ensure_list,
    }
)

DELETE_GATE_AREA_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("gate_area_id"): vol.All(cv.string, vol.Length(min=1, max=128)),
    }
)

DEVICE_ONLY_SCHEMA = vol.Schema({vol.Optional("device_id"): cv.string})
RESUME_SCHEMA = DEVICE_ONLY_SCHEMA
CONTINUE_LAST_ORDERED_RUN_SCHEMA = DEVICE_ONLY_SCHEMA
CONTINUE_TASK_SCHEMA = DEVICE_ONLY_SCHEMA
RELEARN_GEOREFERENCE_SCHEMA = DEVICE_ONLY_SCHEMA
REFRESH_MAP_SNAPSHOT_SCHEMA = DEVICE_ONLY_SCHEMA

MARK_NOTIFICATION_READ_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("message_id"): vol.All(cv.string, vol.Length(min=1, max=128)),
    }
)

MARK_ALL_NOTIFICATIONS_READ_SCHEMA = DEVICE_ONLY_SCHEMA


def _hhmm_to_min(value: str) -> int:
    parts = str(value).strip().split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ServiceValidationError(f"Invalid time '{value}' (use HH:MM)")
    return h * 60 + m


def _decoded_map_zone_ids(coordinator) -> set[int]:
    """Return zone IDs only when they come from a successfully decoded map.

    ``coordinator.data['zones']`` can fall back to a user Options list or the
    currently selected partitions. Those sources are intentionally partial, so
    they must never be used to reject an otherwise valid mower command.
    """
    map_data = (coordinator.data or {}).get("map")
    if not isinstance(map_data, dict):
        return set()
    return {
        int(zone["id"])
        for zone in map_data.get("zones") or []
        if isinstance(zone, dict) and zone.get("id") is not None
    }


def _validate_zone_ids(coordinator, zone_ids: list[int]) -> None:
    """Reject unknown explicit zone IDs only when the decoded map proves them wrong."""
    known_zone_ids = _decoded_map_zone_ids(coordinator)
    if not known_zone_ids:
        return
    unknown = sorted({int(zone_id) for zone_id in zone_ids} - known_zone_ids)
    if unknown:
        raise ServiceValidationError(
            "Unknown zone id(s): "
            f"{', '.join(str(value) for value in unknown)}. "
            "Use the internal zone IDs reported by the decoded Navimower map."
        )


def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services once, including services added by upgrades."""

    def _resolve_coordinator(call: ServiceCall):
        store = hass.data.get(DOMAIN) or {}
        coords = [
            value
            for key, value in store.items()
            if not str(key).startswith("_")
            and hasattr(value, "entry")
            and hasattr(value, "client")
        ]
        device_id = call.data.get("device_id")
        if device_id:
            device = dr.async_get(hass).async_get(device_id)
            if device:
                for entry_id in device.config_entries:
                    if entry_id in store:
                        return store[entry_id]
            raise ServiceValidationError("device_id is not a Navimower mower")
        if len(coords) == 1:
            return coords[0]
        raise ServiceValidationError(
            "Multiple Navimow mowers configured: pass device_id to choose one"
        )
    def _note_activity(
        coordinator,
        *,
        source: str,
        message: str,
        context=None,
    ) -> None:
        manager = getattr(coordinator, "activity_context_manager", None)
        if manager is not None:
            manager.note_command(
                source=source,
                message=message,
                context=context,
            )


    async def _set_schedule(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
        day_num = _WEEKDAY_TO_NUM[call.data["day"]]
        enabled = call.data["enabled"]
        periods = []
        for p in call.data.get("periods", []):
            try:
                start_min = _hhmm_to_min(p["start"])
                end_min = _hhmm_to_min(p["end"])
            except (IndexError, TypeError, ValueError) as err:
                raise ServiceValidationError(
                    f"Invalid schedule time; use HH:MM: {err}"
                ) from err
            if end_min == 0:
                end_min = 1440
            if start_min % 15 or end_min % 15:
                raise ServiceValidationError(
                    "Schedule times must use 15-minute increments"
                )
            zone_ids = list(p.get("zones") or [])
            if enabled:
                if end_min <= start_min:
                    raise ServiceValidationError(
                        f"Schedule end must be after start ({p['start']}–{p['end']})"
                    )
                _validate_zone_ids(coordinator, zone_ids)
            periods.append(
                {
                    "start_min": start_min,
                    "end_min": end_min,
                    "zone_ids": zone_ids,
                }
            )
        # Semantic validation is only for a schedule being enabled. A day that
        # already contains a stale/overlapping vendor-app plan must still be
        # possible to switch off instead of being trapped behind our validator.
        if enabled:
            periods.sort(key=lambda item: item["start_min"])
            for previous, current in zip(periods, periods[1:]):
                if current["start_min"] < previous["end_min"]:
                    raise ServiceValidationError("Schedule periods may not overlap")
        try:
            await coordinator.async_send(
                coordinator.client.set_day_schedule,
                coordinator.sn,
                coordinator.vehicle_type,
                day_num,
                enabled,
                periods,
            )
        except Exception as err:
            raise HomeAssistantError(f"Navimow set_schedule failed: {err}") from err

    async def _mow(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
        requested_zones = [int(z) for z in call.data.get("zones") or []]
        _validate_zone_ids(coordinator, requested_zones)
        zones = list(requested_zones)
        requested_ordered = bool(zones)
        ordered = requested_ordered and supports_ordered_zone_mowing(
            (coordinator.data or {}).get("model")
            or coordinator.entry.data.get("model"),
            coordinator.vehicle_type,
        )
        if not zones:
            zones = [
                z["id"]
                for z in (coordinator.data or {}).get("zones") or []
                if z.get("id") is not None
            ]
        if not zones:
            raise ServiceValidationError(
                "No mowing zones known yet — wait for the mower to report its map, "
                "or pass explicit zone ids."
            )
        # Frontend buttons can be gray, but API/automations must have the
        # same server-side protection against stale or conflicting state.
        command_snapshot = coordinator.data or {}
        if not confirmed_paused_vendor_task(command_snapshot):
            mqtt_state = command_snapshot.get("mqtt_vehicle_state")
            activity = command_snapshot.get("activity")
            if mqtt_state == 4 or activity == ACTIVITY_MOWING:
                raise ServiceValidationError(
                    "The mower is already mowing. Pause or dock before "
                    "starting a different mowing task."
                )
            if mqtt_state == 5 or activity == ACTIVITY_RETURNING:
                raise ServiceValidationError(
                    "The mower is returning to dock. Wait for docking "
                    "before starting another mowing task."
                )
        paused_action = paused_mow_request_decision(
            command_snapshot, requested_zones, reset=call.data["reset"],
        )
        if paused_action == "blocked":
            raise ServiceValidationError(
                "Mow with Reset OFF cannot change the selected zones while the "
                "vendor task is paused. Resume the existing task instead, or "
                "explicitly start a new cycle with Reset ON."
            )
        if paused_action == "vendor_resume":
            await async_resume_task(coordinator, source="navimower.mow_paused")
            return
        partition_ids = encode_partition_ids(zones)
        partition_setup = mow_setup(reset=call.data["reset"], ordered=ordered)
        coordinator.begin_mow_command_trace(
            source="navimower.mow",
            requested_zone_ids=requested_zones,
            resolved_zone_ids=zones,
            reset=call.data["reset"],
            ordered=ordered,
            partition_ids_hex=partition_ids,
            partition_setup=partition_setup,
        )
        zone_names = {
            int(row["id"]): str(row.get("name") or f"Zone {row['id']}")
            for row in (coordinator.data or {}).get("zones") or []
            if isinstance(row, dict) and row.get("id") is not None
        }
        requested_names = [zone_names.get(value, f"Zone {value}") for value in zones]
        target_text = ", ".join(requested_names) if requested_names else "all zones"
        _note_activity(
            coordinator,
            source="navimower.mow",
            message=f"Navimower Mow action requested mowing {target_text}.",
            context=call.context,
        )
        coordinator.set_pending_activity(ACTIVITY_MOWING)
        coordinator.set_command_target(
            requested_zones if requested_ordered else [], source="navimower.mow"
        )
        try:
            result = await coordinator.async_send(
                coordinator.client.mow_zones,
                coordinator.sn,
                partition_ids,
                partition_setup,
            )
            coordinator.record_mow_command_result(result)
            if call.data["reset"]:
                coordinator.defer_mow_cycle_reset(source="navimower.mow_reset")
        except Exception as err:
            coordinator.record_mow_command_error(err)
            coordinator.clear_pending_activity()
            if requested_ordered:
                coordinator.clear_command_target()
            raise HomeAssistantError(f"Navimow mow failed: {err}") from err

    async def _continue_last_ordered_run_for(
        coordinator,
        *,
        source: str,
    ) -> None:
        run = coordinator.last_ordered_run()
        if not run:
            raise ServiceValidationError(
                "No retained ordered mowing run is available for this mower."
            )
        if run.get("superseded_at"):
            raise ServiceValidationError(
                "The last ordered run was superseded by a newer mowing command "
                f"({run.get('superseded_by') or 'unknown source'})."
            )
        if run.get("complete"):
            raise ServiceValidationError(
                "All zones in the last ordered mowing run are already complete."
            )
        if not ordered_run_matches_current_task(
            coordinator.data, run,
            active_session=coordinator.history.active_session_metadata(),
        ):
            raise ServiceValidationError(
                "The retained ordered run belongs to an older mowing task. "
                "Use the vendor Resume action for the current task instead."
            )

        try:
            fresh_completion = await coordinator.async_refresh_last_ordered_run_completion()
        except Exception as err:
            raise HomeAssistantError(
                f"Could not refresh per-zone completion before continuation: {err}"
            ) from err
        if not fresh_completion:
            raise HomeAssistantError(
                "Could not confirm fresh per-zone completion; no mowing command was sent."
            )

        run = coordinator.last_ordered_run() or run
        if not ordered_run_matches_current_task(
            coordinator.data, run,
            active_session=coordinator.history.active_session_metadata(),
        ):
            raise ServiceValidationError(
                "A newer vendor task was observed during refresh; "
                "the retained ordered run cannot be replayed."
            )
        zones = coordinator.remaining_last_ordered_run_zone_ids()
        if not zones:
            if run.get("complete"):
                raise ServiceValidationError(
                    "All zones in the last ordered mowing run are already complete."
                )
            raise ServiceValidationError(
                "The last ordered mowing run has no unfinished zones to continue."
            )

        model = (
            (coordinator.data or {}).get("model")
            or coordinator.entry.data.get("model")
        )
        if not supports_ordered_zone_mowing(model, coordinator.vehicle_type):
            raise ServiceValidationError(
                "This mower does not support ordered zone mowing, so the retained "
                "zone order cannot be continued safely."
            )

        _validate_zone_ids(coordinator, zones)
        partition_ids = encode_partition_ids(zones)
        partition_setup = mow_setup(reset=False, ordered=True)
        coordinator.begin_mow_command_trace(
            source=source,
            requested_zone_ids=zones,
            resolved_zone_ids=zones,
            reset=False,
            ordered=True,
            partition_ids_hex=partition_ids,
            partition_setup=partition_setup,
        )
        coordinator.set_pending_activity(ACTIVITY_MOWING)
        coordinator.set_command_target(zones, source=source)
        try:
            result = await coordinator.async_send(
                coordinator.client.mow_zones,
                coordinator.sn,
                partition_ids,
                partition_setup,
            )
            coordinator.record_mow_command_result(result)
        except Exception as err:
            coordinator.record_mow_command_error(err)
            coordinator.clear_pending_activity()
            coordinator.clear_command_target()
            raise HomeAssistantError(
                f"Navimow continue_last_ordered_run failed: {err}"
            ) from err

    async def _continue_last_ordered_run(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
        _note_activity(
            coordinator,
            source="navimower.continue_last_ordered_run",
            message="Navimower requested continuation of the unfinished ordered mowing task.",
            context=call.context,
        )
        await _continue_last_ordered_run_for(
            coordinator,
            source="navimower.continue_last_ordered_run",
        )

    async def _continue_task(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
        retained_sessions = coordinator.history.session_summaries(include_points=False)
        decision = task_resume_decision(
            coordinator.data,
            active_session=coordinator.history.active_session_metadata(),
            retained_session=(
                retained_sessions[-1] if retained_sessions else None
            ),
        )
        controller = getattr(coordinator, "navimower_schedule", None)
        schedule_state = {"enabled": controller.enabled} if controller is not None else None
        decision = guard_managed_schedule_resume(decision, schedule_state)
        if not decision.get("available"):
            raise ServiceValidationError(
                "No resumable mowing task is currently confirmed "
                f"({decision.get('reason') or 'unknown reason'})."
            )

        strategy = decision.get("strategy")
        _note_activity(
            coordinator,
            source="navimower.continue_task",
            message="Navimower requested continuation of the confirmed unfinished mowing task.",
            context=call.context,
        )
        if strategy == RESUME_STRATEGY_ORDERED_RUN:
            await _continue_last_ordered_run_for(
                coordinator,
                source="navimower.continue_task",
            )
            return
        if strategy == RESUME_STRATEGY_VENDOR:
            try:
                await async_resume_task(
                    coordinator,
                    source="navimower.continue_task",
                )
            except Exception as err:
                raise HomeAssistantError(
                    f"Navimow continue_task vendor resume failed: {err}"
                ) from err
            return

        raise ServiceValidationError(
            "The confirmed resumable task has no supported continuation strategy."
        )

    async def _set_schedule_queue(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
        controller = getattr(coordinator, "navimower_schedule", None)
        if controller is None:
            raise ServiceValidationError("Navimower Schedule controller is not available")
        try:
            await controller.async_set_custom_queue(
                [int(v) for v in call.data.get("zones") or []]
            )
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except Exception as err:
            raise HomeAssistantError(
                f"Navimower set_schedule_queue failed: {err}"
            ) from err

    async def _set_gate_area(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
        try:
            channels = upsert_gate_area(
                coordinator.entry.options.get(OPT_CHANNELS),
                gate_area_id=call.data.get("gate_area_id"),
                name=call.data["name"],
                polygon=call.data["polygon"],
            )
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

        options = dict(coordinator.entry.options)
        options[OPT_CHANNELS] = channels
        hass.config_entries.async_update_entry(coordinator.entry, options=options)
        try:
            await hass.config_entries.async_reload(coordinator.entry.entry_id)
        except Exception as err:
            raise HomeAssistantError(
                f"Gate area was saved but Navimower reload failed: {err}"
            ) from err

    async def _delete_gate_area(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
        try:
            channels = delete_gate_area(
                coordinator.entry.options.get(OPT_CHANNELS),
                call.data["gate_area_id"],
            )
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

        options = dict(coordinator.entry.options)
        options[OPT_CHANNELS] = channels
        hass.config_entries.async_update_entry(coordinator.entry, options=options)
        try:
            await hass.config_entries.async_reload(coordinator.entry.entry_id)
        except Exception as err:
            raise HomeAssistantError(
                f"Gate area was deleted but Navimower reload failed: {err}"
            ) from err

    async def _resume(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
        _note_activity(
            coordinator,
            source="navimower.resume",
            message="Navimower Resume action requested the vendor-retained mowing task to continue.",
            context=call.context,
        )
        try:
            await async_resume_task(coordinator, source="navimower.resume")
        except Exception as err:
            raise HomeAssistantError(f"Navimow resume failed: {err}") from err

    async def _mark_notification_read(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
        message_id = str(call.data["message_id"]).strip()
        try:
            await async_mark_notification_read(coordinator, message_id)
        except Exception as err:
            raise HomeAssistantError(
                f"Navimow mark_notification_read failed: {err}"
            ) from err

    async def _mark_all_notifications_read(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
        try:
            await async_mark_all_notifications_read(coordinator)
        except Exception as err:
            raise HomeAssistantError(
                f"Navimow mark_all_notifications_read failed: {err}"
            ) from err

    async def _relearn_georeference(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
        try:
            result = await async_relearn_georeference(coordinator)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except Exception as err:
            raise HomeAssistantError(
                f"Navimower georeference relearn failed: {err}"
            ) from err
        persistent_notification.async_create(
            hass,
            (
                "Navimower georeference calibration was cleared for this mower. "
                "A fresh calibration is now learning from new location samples.\n\n"
                f"Map revision: `{result.get('map_revision')}`\n"
                f"Previous samples: `{result.get('previous_sample_count')}`\n"
                f"Current status: `{result.get('active_status')}`"
            ),
            title="Navimower georeference relearn started",
            notification_id=f"navimower_georeference_relearn_{coordinator.entry.entry_id}",
        )

    async def _refresh_map_snapshot(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
        managers = active_map_snapshot_managers(coordinator)
        if not managers:
            raise ServiceValidationError(
                "No enabled Navimower map snapshot image entity is available."
            )
        try:
            await asyncio.gather(
                *(
                    manager.async_refresh(
                        reason="manual",
                        force=True,
                        require_fresh=True,
                    )
                    for manager in managers
                )
            )
        except Exception as err:
            raise HomeAssistantError(
                f"Navimower map snapshot refresh failed: {err}"
            ) from err

    registrations = (
        (SERVICE_SET_SCHEDULE, _set_schedule, SET_SCHEDULE_SCHEMA),
        (SERVICE_MOW, _mow, MOW_SCHEMA),
        (
            SERVICE_CONTINUE_LAST_ORDERED_RUN,
            _continue_last_ordered_run,
            CONTINUE_LAST_ORDERED_RUN_SCHEMA,
        ),
        (SERVICE_CONTINUE_TASK, _continue_task, CONTINUE_TASK_SCHEMA),
        (SERVICE_SET_SCHEDULE_QUEUE, _set_schedule_queue, SET_SCHEDULE_QUEUE_SCHEMA),
        (SERVICE_SET_GATE_AREA, _set_gate_area, SET_GATE_AREA_SCHEMA),
        (SERVICE_DELETE_GATE_AREA, _delete_gate_area, DELETE_GATE_AREA_SCHEMA),
        (SERVICE_RESUME, _resume, RESUME_SCHEMA),
        (SERVICE_MARK_NOTIFICATION_READ, _mark_notification_read, MARK_NOTIFICATION_READ_SCHEMA),
        (SERVICE_MARK_ALL_NOTIFICATIONS_READ, _mark_all_notifications_read, MARK_ALL_NOTIFICATIONS_READ_SCHEMA),
        (SERVICE_RELEARN_GEOREFERENCE, _relearn_georeference, RELEARN_GEOREFERENCE_SCHEMA),
        (SERVICE_REFRESH_MAP_SNAPSHOT, _refresh_map_snapshot, REFRESH_MAP_SNAPSHOT_SCHEMA),
    )
    for service, handler, schema in registrations:
        if not hass.services.has_service(DOMAIN, service):
            hass.services.async_register(DOMAIN, service, handler, schema=schema)
