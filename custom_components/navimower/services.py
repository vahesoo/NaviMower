"""Services for Navimower.

- ``navimower.set_schedule`` writes one weekday's plan (enabled + one or more
  time periods, each optionally restricted to zones) via the proven
  save-set-data format.
- ``navimower.mow`` starts mowing now: chosen zones and a ``reset`` flag
  (True = restart from scratch, False = continue). On models that support
  custom sequencing, listing zones explicitly also fixes their mowing order.
  First-generation H-series mowers still accept the selected zones but choose
  their order themselves.
- ``navimower.resume`` resumes the vendor-retained interrupted task without
  selecting zones, resetting progress or starting a new Navimower mowing cycle.
- ``navimower.mark_notification_read`` opens one Device notification through the
  same encrypted detail route as the official app and then refreshes the Device
  feed to confirm the resulting vendor read state.
- ``navimower.mark_all_notifications_read`` executes the official app's Mark all
  as read request for the selected mower/account and refreshes the Device feed.

Diagnostics are exposed through Home Assistant's native Download diagnostics
flow rather than custom development services.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .runtime import install_runtime_extensions
from .const import (
    ACTIVITY_MOWING,
    DOMAIN,
    encode_partition_ids,
    mow_setup,
)
from .model_support import supports_ordered_zone_mowing
from .notification_actions import (
    async_mark_all_notifications_read,
    async_mark_notification_read,
)
from .resume import async_resume_task

install_runtime_extensions()

SERVICE_SET_SCHEDULE = "set_schedule"
SERVICE_MOW = "mow"
SERVICE_RESUME = "resume"
SERVICE_MARK_NOTIFICATION_READ = "mark_notification_read"
SERVICE_MARK_ALL_NOTIFICATIONS_READ = "mark_all_notifications_read"

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

RESUME_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
    }
)

MARK_NOTIFICATION_READ_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("message_id"): vol.All(cv.string, vol.Length(min=1, max=128)),
    }
)

MARK_ALL_NOTIFICATIONS_READ_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
    }
)


def _hhmm_to_min(value: str) -> int:
    parts = str(value).strip().split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ServiceValidationError(f"Invalid time '{value}' (use HH:MM)")
    return h * 60 + m


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

    async def _set_schedule(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
        day_num = _WEEKDAY_TO_NUM[call.data["day"]]
        enabled = call.data["enabled"]
        periods = []
        known_zone_ids = {
            int(zone["id"])
            for zone in (coordinator.data or {}).get("zones") or []
            if zone.get("id") is not None
        }
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
            if end_min <= start_min:
                raise ServiceValidationError(
                    f"Schedule end must be after start ({p['start']}–{p['end']})"
                )
            zone_ids = list(p.get("zones") or [])
            unknown = [
                zone_id
                for zone_id in zone_ids
                if known_zone_ids and zone_id not in known_zone_ids
            ]
            if unknown:
                raise ServiceValidationError(
                    f"Unknown zone id(s): {', '.join(str(value) for value in unknown)}"
                )
            periods.append(
                {
                    "start_min": start_min,
                    "end_min": end_min,
                    "zone_ids": zone_ids,
                }
            )
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
                coordinator.start_new_mowing_cycle(
                    zones, source="navimower.mow_reset"
                )
        except Exception as err:
            coordinator.record_mow_command_error(err)
            coordinator.clear_pending_activity()
            if requested_ordered:
                coordinator.clear_command_target()
            raise HomeAssistantError(f"Navimow mow failed: {err}") from err

    async def _resume(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
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

    if not hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_SCHEDULE,
            _set_schedule,
            schema=SET_SCHEDULE_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_MOW):
        hass.services.async_register(DOMAIN, SERVICE_MOW, _mow, schema=MOW_SCHEMA)
    if not hass.services.has_service(DOMAIN, SERVICE_RESUME):
        hass.services.async_register(
            DOMAIN,
            SERVICE_RESUME,
            _resume,
            schema=RESUME_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_MARK_NOTIFICATION_READ):
        hass.services.async_register(
            DOMAIN,
            SERVICE_MARK_NOTIFICATION_READ,
            _mark_notification_read,
            schema=MARK_NOTIFICATION_READ_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_MARK_ALL_NOTIFICATIONS_READ):
        hass.services.async_register(
            DOMAIN,
            SERVICE_MARK_ALL_NOTIFICATIONS_READ,
            _mark_all_notifications_read,
            schema=MARK_ALL_NOTIFICATIONS_READ_SCHEMA,
        )
