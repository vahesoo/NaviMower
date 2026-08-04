"""Select platform for Navimower."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NavimowCoordinator
from .entity import NavimowEntity

_LOGGER = logging.getLogger(__name__)

ALL_ZONES = "All zones"


@dataclass(frozen=True, kw_only=True)
class NavimowSelectDescription(SelectEntityDescription):
    """A multi-value MowerSettingBean setting."""

    value_fn: Callable[[dict], int | str | None]
    write_key: str
    value_map: dict[str, int | str]
    robot_numeric: bool = False
    raw_read_key: str | None = None


SETTING_SELECTS: tuple[NavimowSelectDescription, ...] = (
    NavimowSelectDescription(
        key="work_mode",
        name="Work mode",
        icon="mdi:grass",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: None,
        raw_read_key="mode",
        write_key="mode",
        value_map={
            "standard": "02",
            "efficient": "03",
            "precision": "04",
        },
        robot_numeric=False,
    ),
    NavimowSelectDescription(
        key="night_light_level",
        translation_key="night_light_level",
        icon="mdi:brightness-6",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("night_light_level"),
        write_key="nightLightLevel",
        value_map={"dim": 0, "very_dim": 1},
        robot_numeric=False,
    ),
    NavimowSelectDescription(
        key="weather_sensitivity",
        translation_key="weather_sensitivity",
        icon="mdi:weather-partly-rainy",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("weather_sensitivity"),
        write_key="weatherSensitivity",
        value_map={"drizzle": 0, "light": 1, "moderate": 2},
        robot_numeric=True,
    ),
)


def _raw_setting(data: dict, key: str) -> Any:
    raw = data.get("raw") or {}
    set_list = raw.get("set_list") or {}
    return set_list.get(key) if isinstance(set_list, dict) else None


def _read_value(desc: NavimowSelectDescription, data: dict) -> int | str | None:
    if desc.raw_read_key is not None:
        value = _raw_setting(data, desc.raw_read_key)
        return None if value is None else str(value)
    return desc.value_fn(data.get("settings") or {})


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: NavimowCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}
    entities: list[SelectEntity] = [NavimowZoneSelect(coordinator)]
    entities += [
        NavimowSettingSelect(coordinator, desc)
        for desc in SETTING_SELECTS
        if _read_value(desc, data) is not None
    ]
    async_add_entities(entities)


class NavimowZoneSelect(NavimowEntity, SelectEntity):
    """Pick a zone (or all) to start mowing."""

    _attr_translation_key = "zone"
    _attr_icon = "mdi:select-marker"

    def __init__(self, coordinator: NavimowCoordinator) -> None:
        super().__init__(coordinator, "zone")

    def _zones(self) -> list[dict]:
        return self.data.get("zones") or []

    @property
    def options(self) -> list[str]:
        zones = self._zones()
        if not zones:
            return []
        return [z["name"] for z in zones] + [ALL_ZONES]

    @property
    def current_option(self) -> str | None:
        zones = self._zones()
        if not zones:
            return None
        selected = self.coordinator.selected_zone_ids or []
        if not selected:
            return ALL_ZONES
        if set(selected) == {z["id"] for z in zones}:
            return ALL_ZONES
        if len(selected) == 1:
            for zone in zones:
                if zone["id"] == selected[0]:
                    return zone["name"]
        return None

    @property
    def available(self) -> bool:
        return super().available and bool(self._zones())

    async def async_select_option(self, option: str) -> None:
        zones = self._zones()
        if option == ALL_ZONES:
            region_ids: list[int] = []
        else:
            match = next((z for z in zones if z["name"] == option), None)
            if match is None:
                _LOGGER.warning("Unknown zone option %s", option)
                return
            region_ids = [match["id"]]
        self.coordinator.selected_zone_ids = region_ids
        self.async_write_ha_state()


class NavimowSettingSelect(NavimowEntity, SelectEntity):
    """A multi-value mower setting."""

    entity_description: NavimowSelectDescription

    def __init__(
        self, coordinator: NavimowCoordinator, description: NavimowSelectDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._attr_options = list(description.value_map)
        self._reverse = {value: option for option, value in description.value_map.items()}

    @property
    def current_option(self) -> str | None:
        value = _read_value(self.entity_description, self.data)
        return self._reverse.get(value)

    async def async_select_option(self, option: str) -> None:
        value = self.entity_description.value_map[option]
        key = self.entity_description.write_key
        if self.entity_description.robot_numeric:
            robot_value: int | str = int(value)
        elif isinstance(value, int):
            robot_value = f"{value:02d}"
        else:
            robot_value = str(value)
        await self.coordinator.async_send(
            self.coordinator.client.send_setting_device,
            self._sn,
            {key: robot_value},
        )
        await self.coordinator.async_send(
            self.coordinator.client.save_setting_iot,
            self._sn,
            self.coordinator.vehicle_type,
            {key: value},
        )
