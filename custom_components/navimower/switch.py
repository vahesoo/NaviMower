"""Switch platform for Navimower: cloud settings toggles.

Two write families, both via ``/vehicle/set/save-set-data``:

* legacy switches (nightMow/rain/sound/power-saving) use the *plain* form with a
  zero-padded boolean string ('01'/'00');
* "modern" MowerSettingBean toggles use ``operation_type:"iot_set"`` with a
  per-key value encoding and are sent to the robot first so the change applies.

Feature detection is based on the values reported by the mower. A few settings
that are not copied into the coordinator settings snapshot are read directly
from the sanitized private-cloud ``set_list`` payload.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NavimowCoordinator
from .entity import NavimowEntity


@dataclass(frozen=True, kw_only=True)
class NavimowSwitchDescription(SwitchEntityDescription):
    """Switch description mapping a reported value to a write key."""

    value_fn: Callable[[dict], bool | None]
    write_key: str
    proven: bool = False
    iot: bool = False
    numeric: bool = False
    robot_key: str | None = None
    robot_numeric: bool = True
    enabled_default: bool | None = None
    assumed: bool = False
    gate_key: str | None = None
    raw_read_key: str | None = None


SWITCHES: tuple[NavimowSwitchDescription, ...] = (
    NavimowSwitchDescription(
        key="mowing_schedule_enabled",
        translation_key="mowing_schedule_enabled",
        icon="mdi:calendar-clock",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("schedule_enabled"),
        write_key="startPlan",
        iot=True,
        numeric=False,
        robot_numeric=False,
        enabled_default=True,
    ),
    NavimowSwitchDescription(
        key="night_mow",
        translation_key="night_mow",
        icon="mdi:weather-night",
        value_fn=lambda s: s.get("night_mow"),
        write_key="nightMowSwitch",
        proven=True,
        iot=True,
        numeric=True,
    ),
    NavimowSwitchDescription(
        key="rain_sensor",
        translation_key="rain_sensor",
        icon="mdi:weather-rainy",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("rain_sensor"),
        write_key="rainSensor",
        proven=True,
    ),
    NavimowSwitchDescription(
        key="rain_detection",
        translation_key="rain_detection",
        icon="mdi:weather-pouring",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("rain_detection"),
        write_key="rainDetectionSwitch",
        proven=True,
    ),
    NavimowSwitchDescription(
        key="sound",
        translation_key="sound",
        icon="mdi:volume-high",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("sound"),
        write_key="soundSwitch",
        proven=True,
        iot=True,
    ),
    NavimowSwitchDescription(
        key="power_saving",
        translation_key="power_saving",
        icon="mdi:leaf",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("power_saving"),
        write_key="lowPowerSet",
        proven=True,
        iot=True,
        numeric=True,
    ),
    NavimowSwitchDescription(
        key="child_lock",
        translation_key="child_lock",
        icon="mdi:account-lock",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("child_lock"),
        write_key="childLock",
        iot=True,
        enabled_default=True,
    ),
    NavimowSwitchDescription(
        key="lift_alarm",
        translation_key="lift_alarm",
        icon="mdi:alarm-light",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("lift_alarm"),
        write_key="liftSwitch",
        iot=True,
        enabled_default=True,
    ),
    NavimowSwitchDescription(
        key="mowing_cycle",
        translation_key="mowing_cycle",
        icon="mdi:sync",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("mowing_cycle"),
        write_key="mowingCycle",
        iot=True,
        robot_numeric=False,
        enabled_default=True,
    ),
    NavimowSwitchDescription(
        key="frost_delay",
        translation_key="frost_delay",
        icon="mdi:snowflake-alert",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("frost_delay"),
        write_key="frostSwitch",
        iot=True,
        numeric=True,
        enabled_default=True,
    ),
    NavimowSwitchDescription(
        key="snow_delay",
        translation_key="snow_delay",
        icon="mdi:snowflake",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("snow_delay"),
        write_key="snowSwitch",
        iot=True,
        numeric=True,
        enabled_default=True,
    ),
    NavimowSwitchDescription(
        key="storm_delay",
        translation_key="storm_delay",
        icon="mdi:weather-windy",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("storm_delay"),
        write_key="stormSwitch",
        iot=True,
        numeric=True,
        enabled_default=True,
    ),
    NavimowSwitchDescription(
        key="high_temp_delay",
        translation_key="high_temp_delay",
        icon="mdi:thermometer-high",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("high_temp_delay"),
        write_key="highTempSwitch",
        iot=True,
        numeric=True,
        enabled_default=True,
    ),
    NavimowSwitchDescription(
        key="efls",
        translation_key="efls",
        icon="mdi:cctv",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("efls"),
        write_key="slamSwitch",
        iot=True,
        numeric=True,
        enabled_default=True,
    ),
    NavimowSwitchDescription(
        key="obstacle_avoidance",
        translation_key="obstacle_avoidance",
        icon="mdi:eye-off-outline",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("obstacle_avoid"),
        write_key="cptSwitch",
        iot=True,
        numeric=True,
        enabled_default=True,
    ),
    NavimowSwitchDescription(
        key="traction_control",
        translation_key="traction_control",
        icon="mdi:car-traction-control",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("traction"),
        write_key="tractionControl",
        robot_key="tcsSwitch",
        iot=True,
        numeric=True,
        enabled_default=True,
    ),
    NavimowSwitchDescription(
        key="animal_protection",
        translation_key="animal_protection",
        icon="mdi:paw",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: None,
        raw_read_key="animalProtection",
        write_key="animalProtection",
        iot=True,
        numeric=True,
        enabled_default=True,
    ),
    NavimowSwitchDescription(
        key="night_light",
        translation_key="night_light",
        icon="mdi:lightbulb-night-outline",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: None,
        raw_read_key="lightSwitch",
        write_key="lightSwitch",
        iot=True,
        numeric=True,
        enabled_default=True,
    ),
    NavimowSwitchDescription(
        key="weather_rain",
        translation_key="weather_rain",
        icon="mdi:weather-rainy",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("weather_switch"),
        write_key="weatherSwitch",
        iot=True,
        numeric=True,
        enabled_default=True,
    ),
    NavimowSwitchDescription(
        key="rain_delay_mode",
        translation_key="rain_delay_mode",
        icon="mdi:timer-sand",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("rain_behavior"),
        write_key="delayedPileSwitch",
        iot=True,
        numeric=True,
        enabled_default=True,
    ),
)


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "01", "true", "on", "yes"}:
        return True
    if text in {"0", "00", "false", "off", "no", ""}:
        return False
    return None


def _raw_setting(data: dict, key: str) -> Any:
    raw = data.get("raw") or {}
    set_list = raw.get("set_list") or {}
    return set_list.get(key) if isinstance(set_list, dict) else None


def _read_value(desc: NavimowSwitchDescription, data: dict) -> bool | None:
    if desc.raw_read_key is not None:
        return _as_bool(_raw_setting(data, desc.raw_read_key))
    return desc.value_fn(data.get("settings") or {})


def _present(desc: NavimowSwitchDescription, data: dict) -> bool:
    if desc.proven:
        return True
    settings = data.get("settings") or {}
    if desc.raw_read_key is not None:
        return _raw_setting(data, desc.raw_read_key) is not None
    if desc.gate_key is not None:
        return settings.get(desc.gate_key) is not None
    return desc.value_fn(settings) is not None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: NavimowCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}
    entities = [
        NavimowSwitch(coordinator, desc) for desc in SWITCHES if _present(desc, data)
    ]
    async_add_entities(entities)


class NavimowSwitch(NavimowEntity, SwitchEntity):
    """A boolean cloud setting."""

    entity_description: NavimowSwitchDescription

    def __init__(
        self, coordinator: NavimowCoordinator, description: NavimowSwitchDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._attr_entity_registry_enabled_default = (
            description.enabled_default
            if description.enabled_default is not None
            else description.proven
        )
        self._attr_assumed_state = description.assumed
        self._optimistic: bool | None = None

    @property
    def is_on(self) -> bool | None:
        if self.entity_description.assumed:
            return self._optimistic
        return _read_value(self.entity_description, self.data)

    async def _write(self, on: bool) -> None:
        desc = self.entity_description
        if desc.iot:
            robot_val: Any = (
                (1 if on else 0) if desc.robot_numeric else ("1" if on else "0")
            )
            await self.coordinator.async_send(
                self.coordinator.client.send_setting_device,
                self._sn,
                {desc.robot_key or desc.write_key: robot_val},
            )
            await self.coordinator.async_send(
                self.coordinator.client.set_iot_bool,
                self._sn,
                self.coordinator.vehicle_type,
                desc.write_key,
                on,
                desc.numeric,
            )
        else:
            await self.coordinator.async_send(
                self.coordinator.client.set_bool_setting,
                self._sn,
                desc.write_key,
                on,
            )
        if desc.assumed:
            self._optimistic = on
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)
