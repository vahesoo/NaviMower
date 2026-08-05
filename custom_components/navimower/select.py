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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NavimowCoordinator
from .entity import NavimowEntity
from .setting_write import async_write_settings

_LOGGER = logging.getLogger(__name__)

ALL_ZONES = "All zones"

RAIN_DELAY_VALUES: dict[str, str] = {
    "15 min": "01",
    "30 min": "02",
    "1 h": "04",
    "2 h": "08",
    "3 h": "0C",
    "4 h": "10",
    "5 h": "14",
    "6 h": "18",
    "7 h": "1C",
    "8 h": "20",
    "9 h": "24",
    "10 h": "28",
    "11 h": "2C",
    "12 h": "30",
    "14 h": "38",
    "16 h": "40",
    "18 h": "48",
    "20 h": "50",
    "22 h": "58",
    "24 h": "60",
}

# The app exposes only quarter-hour choices from midnight through 12:45.
FROST_TIME_VALUES: dict[str, int] = {
    f"{minutes // 60:02d}:{minutes % 60:02d}": minutes // 15
    for minutes in range(0, 12 * 60 + 46, 15)
}

# Do Not Disturb start/end choices cover the full day in 15-minute steps.
DAY_TIME_VALUES: dict[str, int] = {
    f"{minutes // 60:02d}:{minutes % 60:02d}": minutes // 15
    for minutes in range(0, 24 * 60, 15)
}


@dataclass(frozen=True, kw_only=True)
class NavimowSelectDescription(SelectEntityDescription):
    """A multi-value MowerSettingBean setting."""

    value_fn: Callable[[dict], int | str | None]
    write_key: str
    value_map: dict[str, int | str]
    robot_numeric: bool = False
    robot_hex: bool = False
    cloud_hex: bool = False
    cloud_string: bool = False
    raw_read_key: str | None = None
    compound_index: int | None = None
    models: tuple[str, ...] = ()


SETTING_SELECTS: tuple[NavimowSelectDescription, ...] = (
    NavimowSelectDescription(
        key="rain_delay_time",
        name="Wait time after rain",
        icon="mdi:timer-pause",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: None,
        raw_read_key="delayedPileSet",
        write_key="delayedPileSet",
        # Both mower and cloud use the quarter-hour count encoded as hex.
        value_map=RAIN_DELAY_VALUES,
    ),
    NavimowSelectDescription(
        key="frost_delay_until",
        name="Won't mow until after frost",
        icon="mdi:clock-alert-outline",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: None,
        raw_read_key="frostDelayTime",
        write_key="frostDelayTime",
        value_map=FROST_TIME_VALUES,
        # The mower command expects the quarter-hour count as a hex string;
        # set-list and iot_set use the decimal integer.
        robot_hex=True,
    ),
    NavimowSelectDescription(
        key="quiet_period_start",
        name="Quiet period starts",
        icon="mdi:clock-start",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: None,
        raw_read_key="dndPeriod",
        write_key="dndPeriod",
        value_map=DAY_TIME_VALUES,
        compound_index=0,
    ),
    NavimowSelectDescription(
        key="quiet_period_end",
        name="Quiet period ends",
        icon="mdi:clock-end",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: None,
        raw_read_key="dndPeriod",
        write_key="dndPeriod",
        value_map=DAY_TIME_VALUES,
        compound_index=1,
    ),
    NavimowSelectDescription(
        key="work_mode",
        translation_key="work_mode",
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
    ),
    # H215 exposes the app's three-level Night light brightness control through
    # lightIntensity. Keep the existing night_light_level unique ID so upgrades
    # do not create a replacement entity.
    NavimowSelectDescription(
        key="night_light_level",
        name="Night light brightness",
        icon="mdi:brightness-4",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: None,
        raw_read_key="lightIntensity",
        write_key="lightIntensity",
        value_map={
            "Default": "0",
            "Dim": "1",
            "Extra dim": "2",
        },
        models=("H215",),
    ),
    # X390 exposes a separate two-level Brightness control through
    # nightLightLevel. Its set_list may also contain dormant lightIntensity, so
    # the model gate is required in addition to field presence.
    NavimowSelectDescription(
        key="light_brightness",
        name="Brightness",
        icon="mdi:brightness-6",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: None,
        raw_read_key="nightLightLevel",
        write_key="nightLightLevel",
        value_map={
            "Dim": 0,
            "Extra dim": 1,
        },
        models=("X390",),
    ),
    NavimowSelectDescription(
        key="edge_sense_mode",
        name="Edge sense mode",
        icon="mdi:vector-line",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: None,
        raw_read_key="edgeSenselevel",
        write_key="edgeSenselevel",
        value_map={
            "Standard": 0,
            "Cautious": 1,
            "Extreme": 2,
        },
        robot_numeric=True,
        models=("H215",),
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


def _set_list(data: dict) -> dict[str, Any] | None:
    raw = data.get("raw") or {}
    value = raw.get("set_list")
    return value if isinstance(value, dict) else None


def _raw_setting(data: dict, key: str) -> Any:
    set_list = _set_list(data)
    return set_list.get(key) if set_list is not None else None


def _model_supported(desc: NavimowSelectDescription, data: dict) -> bool:
    if not desc.models:
        return True
    model = str(data.get("model") or "").strip().casefold()
    return model in {candidate.casefold() for candidate in desc.models}


def _decode_dnd_period(value: Any) -> tuple[int, int] | None:
    """Decode the app's two hexadecimal quarter-hour bytes."""
    text = str(value).strip().upper() if value is not None else ""
    if len(text) != 4:
        return None
    try:
        start = int(text[:2], 16)
        end = int(text[2:], 16)
    except ValueError:
        return None
    if not 0 <= start < 96 or not 0 <= end < 96:
        return None
    return start, end


def _normalize_raw_value(
    desc: NavimowSelectDescription, value: Any
) -> int | str | None:
    """Normalize set-list values to the type used by the select value map."""
    if desc.compound_index is not None:
        period = _decode_dnd_period(value)
        return None if period is None else period[desc.compound_index]
    if value is None:
        return None
    mapped = tuple(desc.value_map.values())
    if any(isinstance(candidate, int) for candidate in mapped):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
    return str(value).strip().upper()


def _read_value(desc: NavimowSelectDescription, data: dict) -> int | str | None:
    if desc.raw_read_key is not None:
        return _normalize_raw_value(desc, _raw_setting(data, desc.raw_read_key))
    return desc.value_fn(data.get("settings") or {})


def _supported(desc: NavimowSelectDescription, data: dict) -> bool:
    return _model_supported(desc, data) and _read_value(desc, data) is not None


def _remove_unsupported_registry_entities(
    hass: HomeAssistant,
    coordinator: NavimowCoordinator,
    supported: set[str],
) -> None:
    """Remove stale setting entities left by an older model mapping."""
    registry = er.async_get(hass)
    for desc in SETTING_SELECTS:
        if desc.key in supported:
            continue
        entity_id = registry.async_get_entity_id(
            "select", DOMAIN, f"{coordinator.sn}_{desc.key}"
        )
        if entity_id is not None:
            registry.async_remove(entity_id)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: NavimowCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}
    supported_descriptions = [
        desc for desc in SETTING_SELECTS if _supported(desc, data)
    ]

    # Cleanup is safe only after the private cloud supplied a real set_list.
    # A temporary endpoint failure must not delete otherwise valid entities.
    if _set_list(data) is not None:
        _remove_unsupported_registry_entities(
            hass, coordinator, {desc.key for desc in supported_descriptions}
        )

    entities: list[SelectEntity] = [NavimowZoneSelect(coordinator)]
    entities.extend(
        NavimowSettingSelect(coordinator, desc) for desc in supported_descriptions
    )
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
        self._attr_entity_registry_enabled_default = True
        self._attr_options = list(description.value_map)
        self._reverse = {value: option for option, value in description.value_map.items()}

    @property
    def current_option(self) -> str | None:
        value = _read_value(self.entity_description, self.data)
        return self._reverse.get(value)

    async def async_select_option(self, option: str) -> None:
        desc = self.entity_description
        value = desc.value_map[option]
        key = desc.write_key

        if desc.compound_index is not None:
            period = _decode_dnd_period(_raw_setting(self.data, key))
            if period is None:
                raise HomeAssistantError(
                    "The Navimow app has not provided a valid quiet period."
                )
            updated = list(period)
            updated[desc.compound_index] = int(value)
            compound = f"{updated[0]:02X}{updated[1]:02X}"
            robot_value: int | str = compound
            cloud_value: int | str = compound
        else:
            if desc.robot_hex:
                robot_value = f"{int(value):02X}"
            elif desc.robot_numeric:
                robot_value = int(value)
            elif isinstance(value, int):
                robot_value = f"{value:02d}"
            else:
                robot_value = str(value)

            if desc.cloud_hex:
                cloud_value = f"{int(value):02X}"
            elif desc.cloud_string:
                cloud_value = str(value)
            else:
                cloud_value = value

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
