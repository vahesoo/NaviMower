"""Services for Navimower.

- ``navimower.set_schedule`` writes one weekday's plan (enabled + one or more
  time periods, each optionally restricted to zones) via the proven
  save-set-data format.
- ``navimower.mow`` starts mowing now: chosen zones and a ``reset`` flag
  (True = riparti da zero / clear progress, False = continua). On models that
  support custom sequencing, listing zones explicitly also fixes their mowing
  order. First-generation H-series mowers still accept the selected zones but
  choose their order themselves.

These back the graphical cards (and automations).
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .diagnostics_export import async_export_diagnostics

from .const import (
    ACTIVITY_MOWING,
    DOMAIN,
    encode_partition_ids,
    mow_setup,
)
from .model_support import supports_ordered_zone_mowing

SERVICE_SET_SCHEDULE = "set_schedule"
SERVICE_MOW = "mow"
SERVICE_EXPORT_DIAGNOSTICS = "export_diagnostics"
SERVICE_MARK_DISCOVERY_EVENT = "mark_discovery_event"

# Navimow weekday numbering is 1=Sun .. 7=Sat.
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
        vol.Required("start"): cv.string,  # "HH:MM"
        vol.Required("end"): cv.string,  # "HH:MM"
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
        # Region ids to mow; empty = all available zones.
        vol.Optional("zones", default=list): vol.All(cv.ensure_list, [vol.Coerce(int)]),
        # True = riparti da zero (clear progress); False = continua.
        vol.Optional("reset", default=True): cv.boolean,
    }
)

EXPORT_DIAGNOSTICS_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Optional("include_compressed_map", default=True): cv.boolean,
    }
)

MARK_DISCOVERY_EVENT_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("name"): cv.string,
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
    """Register integration services once."""
    if hass.services.has_service(DOMAIN, SERVICE_MOW):
        return

    def _resolve_coordinator(call: ServiceCall):
        store = hass.data.get(DOMAIN) or {}
        # ``hass.data[DOMAIN]`` also contains private helper state such as the
        # options snapshot. Only config-entry coordinators are valid service
        # targets.
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
            # An end of "00:00" means end-of-day (24:00 = slot 96), never 0.
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
        except Exception as err:  # noqa: BLE001 - surface a clean error to the UI
            raise HomeAssistantError(f"Navimow set_schedule failed: {err}") from err

    async def _export_diagnostics(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
        try:
            path = await async_export_diagnostics(
                hass,
                coordinator,
                include_compressed_map=call.data["include_compressed_map"],
            )
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Navimower diagnostics export failed: {err}") from err
        persistent_notification.async_create(
            hass,
            (
                "Read-only Navimower diagnostics export completed.\n\n"
                f"File: `{path}`\n\n"
                "The export is sanitized, but review it before publishing."
            ),
            title="Navimower diagnostics export",
            notification_id="navimower_diagnostics_export",
        )

    async def _mark_discovery_event(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(call)
        bridge = getattr(coordinator, "mqtt_bridge", None)
        if bridge is None or not hasattr(bridge, "mark_discovery_event"):
            raise ServiceValidationError("MQTT discovery bridge is not available")
        if not getattr(bridge, "discovery_enabled", False):
            raise ServiceValidationError(
                "Passive discovery is disabled; enable it in Navimower options first"
            )
        bridge.mark_discovery_event(str(call.data["name"]))

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
        except Exception as err:  # noqa: BLE001 - surface a clean error to the UI
            coordinator.record_mow_command_error(err)
            coordinator.clear_pending_activity()
            if requested_ordered:
                coordinator.clear_command_target()
            raise HomeAssistantError(f"Navimow mow failed: {err}") from err

    hass.services.async_register(
        DOMAIN, SERVICE_SET_SCHEDULE, _set_schedule, schema=SET_SCHEDULE_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_MOW, _mow, schema=MOW_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_DIAGNOSTICS,
        _export_diagnostics,
        schema=EXPORT_DIAGNOSTICS_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_MARK_DISCOVERY_EVENT,
        _mark_discovery_event,
        schema=MARK_DISCOVERY_EVENT_SCHEMA,
    )
