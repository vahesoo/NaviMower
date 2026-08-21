"""Local time controls for the Navimower-managed mowing window."""
# Do not create legacy time entities for the retired frost-delay control.
from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NavimowCoordinator
from .entity import NavimowEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NavimowCoordinator = hass.data[DOMAIN][entry.entry_id]
    controller = getattr(coordinator, "navimower_schedule", None)
    if controller is None or not controller.configured:
        registry = er.async_get(hass)
        for key in ("start", "end"):
            entity_id = registry.async_get_entity_id(
                "time", DOMAIN, f"{coordinator.sn}_navimower_schedule_{key}"
            )
            if entity_id is not None:
                registry.async_remove(entity_id)
        return
    async_add_entities(
        [
            NavimowerScheduleTime(coordinator, "start"),
            NavimowerScheduleTime(coordinator, "end"),
        ]
    )


class NavimowerScheduleTime(NavimowEntity, TimeEntity):
    """Start or end of the integration-owned mowing window."""

    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: NavimowCoordinator, key: str) -> None:
        super().__init__(coordinator, f"navimower_schedule_{key}")
        self.controller = coordinator.navimower_schedule
        self._key = key
        self._attr_name = f"Navimower schedule {key}"

    @property
    def native_value(self) -> time:
        return self.controller.start_time if self._key == "start" else self.controller.end_time

    async def async_set_value(self, value: time) -> None:
        await self.controller.async_set_window(self._key, value)
        self.async_write_ha_state()
