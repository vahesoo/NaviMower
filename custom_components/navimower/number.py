"""Number platform for Navimower settings."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NavimowCoordinator
from .entity import NavimowEntity
from .setting_write import async_write_settings


@dataclass(frozen=True, kw_only=True)
class NavimowNumberDescription(NumberEntityDescription):
    """A numeric MowerSettingBean value."""

    value_fn: Callable[[dict], int | None]
    write_key: str
    scale: int = 1
    cloud_hex: bool = False
    cloud_string: bool = False
    robot_hex: bool = True
    robot_numeric: bool = False
    raw_read_key: str | None = None
    raw_base: int = 10
    enabled_default: bool = True


NUMBERS: tuple[NavimowNumberDescription, ...] = (
    # Battery settings
    NavimowNumberDescription(
        key="return_battery_level",
        translation_key="return_battery_level",
        icon="mdi:battery-arrow-down",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=5,
        native_max_value=50,
        native_step=5,
        mode=NumberMode.SLIDER,
        value_fn=lambda s: s.get("return_battery_level"),
        write_key="returnBatteryLevel",
    ),
    NavimowNumberDescription(
        key="charging_limit",
        translation_key="charging_limit",
        icon="mdi:battery-charging-high",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=50,
        native_max_value=100,
        native_step=5,
        mode=NumberMode.SLIDER,
        value_fn=lambda s: s.get("charging_limit"),
        write_key="chargingLimit",
    ),
    # Weather-adaptive settings with regular app ranges.
    NavimowNumberDescription(
        key="snow_delay_time",
        name="Snow delay duration",
        icon="mdi:calendar-clock",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement=UnitOfTime.DAYS,
        native_min_value=1,
        native_max_value=7,
        native_step=1,
        mode=NumberMode.SLIDER,
        value_fn=lambda s: None,
        raw_read_key="snowDelayTime",
        write_key="snowDelayTime",
        scale=24,
        # Home Assistant shows whole days. The mower command still expects the
        # underlying hour count as hexadecimal; cloud set-list stores hours.
        robot_hex=True,
    ),
    NavimowNumberDescription(
        key="maximum_mowing_temperature",
        translation_key="maximum_mowing_temperature",
        icon="mdi:thermometer-high",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=30,
        native_max_value=45,
        native_step=1,
        mode=NumberMode.SLIDER,
        value_fn=lambda s: None,
        raw_read_key="allowMaxTemp",
        write_key="allowMaxTemp",
        # Verified live: decimal text "31" is read by the mower as 0x31 (=49).
        robot_hex=True,
    ),
    # Safety settings
    NavimowNumberDescription(
        key="geo_fence_radius",
        translation_key="geo_fence_radius",
        icon="mdi:map-marker-radius-outline",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement=UnitOfLength.METERS,
        native_min_value=10,
        native_max_value=50,
        native_step=1,
        mode=NumberMode.SLIDER,
        value_fn=lambda s: None,
        raw_read_key="antiTheftRadius",
        write_key="antiTheftRadius",
        # The robot expects hex (20 m -> "14"), while cloud set-list stores the
        # human decimal value as a string (20 m -> "20").
        robot_hex=True,
        cloud_string=True,
        enabled_default=False,
    ),
)


def _raw_setting(data: dict, key: str) -> Any:
    raw = data.get("raw") or {}
    set_list = raw.get("set_list") or {}
    return set_list.get(key) if isinstance(set_list, dict) else None


def _wire_value(desc: NavimowNumberDescription, data: dict) -> int | None:
    value = (
        _raw_setting(data, desc.raw_read_key)
        if desc.raw_read_key is not None
        else desc.value_fn(data.get("settings") or {})
    )
    try:
        if isinstance(value, str):
            return int(value.strip(), desc.raw_base)
        return int(float(value))
    except (TypeError, ValueError):
        return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: NavimowCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}
    async_add_entities(
        NavimowNumber(coordinator, desc)
        for desc in NUMBERS
        if _wire_value(desc, data) is not None
    )


class NavimowNumber(NavimowEntity, NumberEntity):
    """A numeric mower setting."""

    entity_description: NavimowNumberDescription

    def __init__(
        self, coordinator: NavimowCoordinator, description: NavimowNumberDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._attr_entity_registry_enabled_default = description.enabled_default

    @property
    def native_value(self) -> float | None:
        value = _wire_value(self.entity_description, self.data)
        return None if value is None else float(value) / self.entity_description.scale

    async def async_set_native_value(self, value: float) -> None:
        desc = self.entity_description
        wire = int(round(float(value) * desc.scale))
        key = desc.write_key
        if desc.robot_hex:
            robot_value: int | str = f"{wire:02X}"
        elif desc.robot_numeric:
            robot_value = wire
        else:
            robot_value = str(wire)
        if desc.cloud_hex:
            cloud_value: int | str = f"{wire:02X}"
        elif desc.cloud_string:
            cloud_value = str(wire)
        else:
            cloud_value = wire
        await async_write_settings(
            self.coordinator,
            operations=(
                (
                    self.coordinator.client.send_setting_device,
                    (self._sn, {key: robot_value}),
                ),
                (
                    self.coordinator.client.save_setting_iot,
                    (
                        self._sn,
                        self.coordinator.vehicle_type,
                        {key: cloud_value},
                    ),
                ),
            ),
            cache_values={key: cloud_value},
        )
