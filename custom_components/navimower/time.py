"""Time platform for Navimower settings."""
from __future__ import annotations

from datetime import time as dt_time
from typing import Any

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NavimowCoordinator
from .entity import NavimowEntity

FROST_TIME_STEP_MINUTES = 15
FROST_TIME_MAX_MINUTES = 12 * 60 + 45

FROST_TIME = TimeEntityDescription(
    key="frost_delay_until",
    translation_key="frost_delay_until",
    icon="mdi:clock-alert-outline",
    entity_category=EntityCategory.CONFIG,
)


def _raw_setting(data: dict, key: str) -> Any:
    raw = data.get("raw") or {}
    set_list = raw.get("set_list") or {}
    return set_list.get(key) if isinstance(set_list, dict) else None


def _frost_minutes(data: dict) -> int | None:
    value = _raw_setting(data, "frostDelayTime")
    try:
        quarters = int(float(value))
    except (TypeError, ValueError):
        return None
    minutes = quarters * FROST_TIME_STEP_MINUTES
    return minutes if 0 <= minutes <= FROST_TIME_MAX_MINUTES else None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: NavimowCoordinator = hass.data[DOMAIN][entry.entry_id]
    if _frost_minutes(coordinator.data or {}) is not None:
        async_add_entities([NavimowFrostTime(coordinator)])


class NavimowFrostTime(NavimowEntity, TimeEntity):
    """Time of day until which frost protection keeps the mower docked."""

    entity_description = FROST_TIME

    def __init__(self, coordinator: NavimowCoordinator) -> None:
        super().__init__(coordinator, FROST_TIME.key)

    @property
    def native_value(self) -> dt_time | None:
        minutes = _frost_minutes(self.data)
        if minutes is None:
            return None
        return dt_time(hour=minutes // 60, minute=minutes % 60)

    async def async_set_value(self, value: dt_time) -> None:
        if value.second or value.microsecond or value.minute % FROST_TIME_STEP_MINUTES:
            raise HomeAssistantError("Frost time must use 15-minute intervals")
        minutes = value.hour * 60 + value.minute
        if not 0 <= minutes <= FROST_TIME_MAX_MINUTES:
            raise HomeAssistantError(
                "The mower app currently supports frost times from 00:00 to 12:45"
            )
        wire = minutes // FROST_TIME_STEP_MINUTES
        await self.coordinator.async_send(
            self.coordinator.client.send_setting_device,
            self._sn,
            {"frostDelayTime": wire},
        )
        await self.coordinator.async_send(
            self.coordinator.client.save_setting_iot,
            self._sn,
            self.coordinator.vehicle_type,
            {"frostDelayTime": wire},
        )
