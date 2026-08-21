"""Switch platform for Navimower cloud settings.

Legacy boolean settings keep their confirmed plain ``save-set-data`` cloud
format with zero-padded strings (``"01"``/``"00"``). Selected legacy controls
also send the matching value to the mower first so its onboard setting cannot
later overwrite the cloud copy. Modern MowerSettingBean toggles use
``operation_type:"iot_set"`` and are likewise sent to the mower first.

Settings writes use one transaction and delayed cloud readback so an eventually
consistent ``set_list`` cannot briefly restore the previous switch state.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NavimowCoordinator
from .entity import NavimowEntity
from .setting_write import async_write_settings


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
    legacy_device_write: bool = False
    enabled_default: bool = True
    assumed: bool = False
    gate_key: str | None = None
    raw_read_key: str | None = None
    raw_read_path: tuple[str, ...] | None = None
    raw_fallback_keys: tuple[str, ...] = ()
    models: tuple[str, ...] = ()


SWITCHES: tuple[NavimowSwitchDescription, ...] = (
    # Mowing settings
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
    ),
    NavimowSwitchDescription(
        key="night_mow",
        name="Night mowing",
        icon="mdi:weather-night",
        value_fn=lambda s: s.get("night_mow"),
        write_key="nightMowSwitch",
        proven=True,
        legacy_device_write=True,
        raw_read_path=("camerabox", "nightMowSwitch"),
        raw_fallback_keys=("nightMowSwitch", "night_mow_switch"),
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
    ),
    # Weather-adaptive settings
    NavimowSwitchDescription(
        key="rain_detection",
        translation_key="rain_detection",
        icon="mdi:weather-rainy",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("rain_detection"),
        write_key="rainDetectionSwitch",
        proven=True,
        legacy_device_write=True,
    ),
    NavimowSwitchDescription(
        key="rain_sensor",
        translation_key="rain_sensor",
        icon="mdi:water-alert",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("rain_sensor"),
        write_key="rainSensor",
        proven=True,
        legacy_device_write=True,
    ),
    NavimowSwitchDescription(
        key="weather_rain",
        translation_key="weather_rain",
        icon="mdi:weather-partly-rainy",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("weather_switch"),
        write_key="weatherSwitch",
        iot=True,
        numeric=True,
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
    ),
    NavimowSwitchDescription(
        key="snow_delay",
        translation_key="snow_delay",
        icon="mdi:weather-snowy-heavy",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("snow_delay"),
        write_key="snowSwitch",
        iot=True,
        numeric=True,
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
    ),
    # General settings
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
        name="Energy saver",
        icon="mdi:leaf",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("power_saving"),
        write_key="lowPowerSet",
        proven=True,
        iot=True,
        numeric=True,
    ),
    NavimowSwitchDescription(
        key="do_not_disturb",
        name="Do not disturb",
        icon="mdi:volume-off",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: None,
        raw_read_key="dndModeSwitch",
        write_key="dndModeSwitch",
        iot=True,
        numeric=False,
        robot_numeric=False,
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
    ),
    # Safety, navigation and model-specific Lab settings
    NavimowSwitchDescription(
        key="child_lock",
        translation_key="child_lock",
        icon="mdi:account-lock",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("child_lock"),
        write_key="childLock",
        iot=True,
    ),
    NavimowSwitchDescription(
        key="lift_alarm",
        translation_key="lift_alarm",
        icon="mdi:alarm-light",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.get("lift_alarm"),
        write_key="liftSwitch",
        iot=True,
    ),
    NavimowSwitchDescription(
        key="geo_fence_alarm",
        translation_key="geo_fence_alarm",
        icon="mdi:radar",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: None,
        raw_read_key="guard",
        write_key="guard",
        iot=True,
        numeric=False,
        robot_numeric=False,
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
    ),
    NavimowSwitchDescription(
        key="terrain_adapt",
        name="Terrain adapt",
        icon="mdi:terrain",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: None,
        raw_read_key="terrainAdaptSwitch",
        write_key="terrainAdaptSwitch",
        iot=True,
        numeric=True,
        models=("H215",),
    ),
    NavimowSwitchDescription(
        key="edge_sense",
        name="Edge sense",
        icon="mdi:vector-line",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: None,
        raw_read_key="edgeSense",
        write_key="edgeSense",
        iot=True,
        numeric=True,
        models=("H215",),
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


def _set_list(data: dict) -> dict[str, Any] | None:
    raw = data.get("raw") or {}
    value = raw.get("set_list")
    return value if isinstance(value, dict) else None


def _raw_setting(data: dict, key: str) -> Any:
    set_list = _set_list(data)
    return set_list.get(key) if set_list is not None else None


def _raw_setting_path(data: dict, path: tuple[str, ...]) -> Any:
    value: Any = _set_list(data)
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _read_raw_value(desc: NavimowSwitchDescription, data: dict) -> Any:
    if desc.raw_read_path is not None:
        value = _raw_setting_path(data, desc.raw_read_path)
        if value is not None:
            return value
    if desc.raw_read_key is not None:
        value = _raw_setting(data, desc.raw_read_key)
        if value is not None:
            return value
    for key in desc.raw_fallback_keys:
        value = _raw_setting(data, key)
        if value is not None:
            return value
    return None


def _uses_raw_read(desc: NavimowSwitchDescription) -> bool:
    return bool(
        desc.raw_read_key is not None
        or desc.raw_read_path is not None
        or desc.raw_fallback_keys
    )


def _read_value(desc: NavimowSwitchDescription, data: dict) -> bool | None:
    if _uses_raw_read(desc):
        return _as_bool(_read_raw_value(desc, data))
    return desc.value_fn(data.get("settings") or {})


def _model_supported(desc: NavimowSwitchDescription, data: dict) -> bool:
    if not desc.models:
        return True
    model = str(data.get("model") or "").strip().casefold()
    return model in {candidate.casefold() for candidate in desc.models}


def _present(desc: NavimowSwitchDescription, data: dict) -> bool:
    if not _model_supported(desc, data):
        return False
    settings = data.get("settings") or {}
    if _uses_raw_read(desc):
        return _read_raw_value(desc, data) is not None
    if desc.gate_key is not None:
        return settings.get(desc.gate_key) is not None
    return desc.value_fn(settings) is not None


def _remove_unsupported_registry_entities(
    hass: HomeAssistant,
    coordinator: NavimowCoordinator,
    supported: set[str],
) -> None:
    """Remove stale setting entities after a confirmed set_list read."""
    registry = er.async_get(hass)
    for desc in SWITCHES:
        if desc.key in supported:
            continue
        entity_id = registry.async_get_entity_id(
            "switch", DOMAIN, f"{coordinator.sn}_{desc.key}"
        )
        if entity_id is not None:
            registry.async_remove(entity_id)


def _nested_cache_root(
    data: dict, path: tuple[str, ...], value: Any
) -> tuple[str, dict[str, Any]]:
    """Return a copied top-level subtree with one nested value updated."""
    if len(path) < 2:
        raise ValueError("nested cache path must contain at least two keys")
    source = _set_list(data) or {}
    root_key = path[0]
    root = dict(source.get(root_key) or {})
    cursor = root
    for key in path[1:-1]:
        child = dict(cursor.get(key) or {})
        cursor[key] = child
        cursor = child
    cursor[path[-1]] = value
    return root_key, root


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: NavimowCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}
    supported_descriptions = [desc for desc in SWITCHES if _present(desc, data)]

    # Cleanup is safe only after the private cloud supplied a real set_list.
    # A temporary endpoint failure must not delete otherwise valid entities.
    if _set_list(data) is not None:
        _remove_unsupported_registry_entities(
            hass, coordinator, {desc.key for desc in supported_descriptions}
        )

    entities = [NavimowSwitch(coordinator, desc) for desc in supported_descriptions]
    controller = getattr(coordinator, "navimower_schedule", None)
    if controller is not None and controller.configured:
        entities.append(NavimowerScheduleSwitch(coordinator))
    else:
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "switch", DOMAIN, f"{coordinator.sn}_navimower_schedule"
        )
        if entity_id is not None:
            registry.async_remove(entity_id)
    async_add_entities(entities)


class NavimowerScheduleSwitch(NavimowEntity, SwitchEntity):
    """Enable the integration-owned daily mowing window."""

    _attr_name = "Navimower schedule"
    _attr_icon = "mdi:calendar-sync"

    def __init__(self, coordinator: NavimowCoordinator) -> None:
        super().__init__(coordinator, "navimower_schedule")
        self.controller = coordinator.navimower_schedule

    @property
    def is_on(self) -> bool:
        return bool(self.controller.enabled)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.controller.entity_attributes()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_set_enabled(True, reason="home_assistant_switch")
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_set_enabled(False, reason="home_assistant_switch")
        self.async_write_ha_state()


class NavimowSwitch(NavimowEntity, SwitchEntity):
    """A boolean cloud setting."""

    entity_description: NavimowSwitchDescription

    def __init__(
        self, coordinator: NavimowCoordinator, description: NavimowSwitchDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._attr_entity_registry_enabled_default = description.enabled_default
        self._attr_assumed_state = description.assumed
        self._optimistic: bool | None = None

    @property
    def is_on(self) -> bool | None:
        if self.entity_description.assumed:
            return self._optimistic
        return _read_value(self.entity_description, self.data)

    async def _write(self, on: bool) -> None:
        desc = self.entity_description
        if desc.key == "mowing_schedule_enabled" and on:
            controller = getattr(self.coordinator, "navimower_schedule", None)
            if controller is not None and controller.enabled:
                await controller.async_set_enabled(False, reason="native_schedule_enabled_from_home_assistant")
        operations = []
        if desc.iot:
            robot_value: Any = (
                (1 if on else 0) if desc.robot_numeric else ("1" if on else "0")
            )
            cloud_value: Any = (
                (1 if on else 0) if desc.numeric else ("1" if on else "0")
            )
            operations.extend(
                (
                    (
                        self.coordinator.client.send_setting_device,
                        (self._sn, {desc.robot_key or desc.write_key: robot_value}),
                    ),
                    (
                        self.coordinator.client.set_iot_bool,
                        (
                            self._sn,
                            self.coordinator.vehicle_type,
                            desc.write_key,
                            on,
                            desc.numeric,
                        ),
                    ),
                )
            )
        else:
            cloud_value = "01" if on else "00"
            if desc.legacy_device_write:
                robot_value = (
                    (1 if on else 0)
                    if desc.robot_numeric
                    else ("1" if on else "0")
                )
                operations.append(
                    (
                        self.coordinator.client.send_setting_device,
                        (self._sn, {desc.robot_key or desc.write_key: robot_value}),
                    )
                )
            operations.append(
                (
                    self.coordinator.client.set_bool_setting,
                    (self._sn, desc.write_key, on),
                )
            )

        cache_values: dict[str, Any] = {desc.write_key: cloud_value}
        if desc.raw_read_path is not None:
            root_key, root = _nested_cache_root(
                self.data, desc.raw_read_path, cloud_value
            )
            cache_values[root_key] = root
        for key in desc.raw_fallback_keys:
            cache_values[key] = cloud_value

        await async_write_settings(
            self.coordinator,
            operations=operations,
            cache_values=cache_values,
        )
        if desc.assumed:
            self._optimistic = on
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)