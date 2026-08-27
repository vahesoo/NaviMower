"""Button platform for Navimower integration-owned actions."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ACTIVITY_RETURNING, DOMAIN
from .coordinator import NavimowCoordinator
from .entity import NavimowEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NavimowCoordinator = hass.data[DOMAIN][entry.entry_id]
    controller = getattr(coordinator, "navimower_schedule", None)
    if controller is not None and controller.configured:
        async_add_entities([NavimowerScheduleResetButton(coordinator)])
        return

    # Remove a registry row left behind if Schedule setup is later removed.
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "button",
        DOMAIN,
        f"{coordinator.sn}_navimower_schedule_reset",
    )
    if entity_id is not None:
        registry.async_remove(entity_id)


class NavimowerScheduleResetButton(NavimowEntity, ButtonEntity):
    """Explicitly reset the managed scheduler's current round/progress."""

    _attr_name = "Reset schedule progress"
    _attr_icon = "mdi:calendar-refresh"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NavimowCoordinator) -> None:
        super().__init__(coordinator, "navimower_schedule_reset")
        self.controller = coordinator.navimower_schedule

    @property
    def available(self) -> bool:
        if not super().available or not self.controller.configured:
            return False
        data = self.coordinator.data or {}
        return not (
            self.controller._vendor_mowing(data)  # noqa: SLF001
            or data.get("activity") == ACTIVITY_RETURNING
        )

    async def async_press(self) -> None:
        try:
            await self.controller.async_reset_schedule(
                reason="home_assistant_button"
            )
        except RuntimeError as err:
            raise HomeAssistantError(str(err)) from err
