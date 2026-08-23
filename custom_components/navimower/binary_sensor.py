"""Binary sensor platform for Navimower."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .channel import NavimowerChannel
from .gate import NavimowerGate
from .const import DOMAIN, MAP_EDIT_STATES
from .coordinator import NavimowCoordinator
from .custom_area import (
    OPT_CUSTOM_AREAS,
    NavimowerCustomArea,
    parse_custom_areas,
    point_in_polygon,
)
from .entity import NavimowEntity


@dataclass(frozen=True, kw_only=True)
class NavimowBinaryDescription(BinarySensorEntityDescription):
    """Binary sensor description with a value extractor over the snapshot."""

    value_fn: Callable[[dict], bool | None]


BINARY_SENSORS: tuple[NavimowBinaryDescription, ...] = (
    NavimowBinaryDescription(
        key="problem",
        translation_key="problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: d.get("error"),
    ),
    NavimowBinaryDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("online"),
    ),
    NavimowBinaryDescription(
        key="private_cloud_connected",
        translation_key="private_cloud_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("private_cloud_connected"),
    ),
    NavimowBinaryDescription(
        key="oauth_connected",
        translation_key="oauth_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("oauth_connected"),
    ),
    NavimowBinaryDescription(
        key="mqtt_connected",
        translation_key="mqtt_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("mqtt_connected") if d.get("mqtt_configured") else None,
    ),
    NavimowBinaryDescription(
        key="pose_valid",
        translation_key="pose_valid",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (
            None
            if not d.get("mqtt_configured")
            or d.get("mqtt_stream_expected") is False
            else (True if d.get("mqtt_pose_valid") else False)
        ),
    ),
    NavimowBinaryDescription(
        key="docked",
        translation_key="docked",
        icon="mdi:home-import-outline",
        value_fn=lambda d: (
            None
            if str(d.get("state_code") or "") in MAP_EDIT_STATES
            else d.get("docked")
        ),
    ),
    NavimowBinaryDescription(
        key="zone_transition",
        translation_key="zone_transition",
        device_class=BinarySensorDeviceClass.MOVING,
        icon="mdi:map-marker-path",
        value_fn=lambda d: d.get("zone_transition"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: NavimowCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [
        NavimowBinarySensor(coordinator, desc) for desc in BINARY_SENSORS
    ]
    entities.extend(
        NavimowerChannelBinarySensor(coordinator, channel)
        for channel in coordinator.channels
    )
    entities.extend(
        NavimowerCustomAreaBinarySensor(coordinator, area)
        for area in parse_custom_areas(entry.options.get(OPT_CUSTOM_AREAS))
    )
    entities.extend(
        NavimowerGateRequiredBinarySensor(coordinator, gate)
        for gate in coordinator.gates
    )
    async_add_entities(entities)


class NavimowBinarySensor(NavimowEntity, BinarySensorEntity):
    """A boolean derived from the mower snapshot."""

    entity_description: NavimowBinaryDescription

    def __init__(
        self, coordinator: NavimowCoordinator, description: NavimowBinaryDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.data)


class NavimowerChannelBinarySensor(NavimowEntity, BinarySensorEntity):
    """On only while a fresh MQTT pose is inside a configured channel."""

    _attr_device_class = BinarySensorDeviceClass.MOVING
    _attr_icon = "mdi:gate"

    def __init__(self, coordinator: NavimowCoordinator, channel: NavimowerChannel) -> None:
        super().__init__(coordinator, f"channel_{channel.slug}")
        self.channel = channel
        self._attr_name = f"{channel.name} channel"

    @property
    def is_on(self) -> bool | None:
        # A dock can physically sit inside a user-defined channel box.  A fresh
        # stationary pose from that dock must not briefly turn the channel ON.
        # Keep the optimistic start exception so a just-issued mowing command is
        # not suppressed while the private-cloud docked state catches up.
        if (
            self.data.get("docked") is True
            and self.coordinator._pending_activity_value() is None
        ):
            return False
        return self.coordinator.channel_state(self.channel)

    @property
    def available(self) -> bool:
        return super().available and self.is_on is not None

    @property
    def extra_state_attributes(self) -> dict:
        return self.channel.as_dict()


class NavimowerCustomAreaBinarySensor(NavimowEntity, BinarySensorEntity):
    """On while the mower's fresh live X/Y pose is inside a Custom Area."""

    _attr_icon = "mdi:vector-polygon"

    def __init__(self, coordinator: NavimowCoordinator, area: NavimowerCustomArea) -> None:
        super().__init__(coordinator, f"custom_area_{area.slug}")
        self.area = area
        self._attr_name = area.name

    @property
    def is_on(self) -> bool | None:
        position = self.coordinator._fresh_mqtt_position()
        if position is None:
            return None
        return point_in_polygon(position["x"], position["y"], self.area.polygon)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator._fresh_mqtt_position() is not None

    @property
    def extra_state_attributes(self) -> dict:
        return {
            **self.area.as_dict(),
            "position_source": "mqtt",
            "position_age": self.coordinator.pose_age(),
        }


class NavimowerGateRequiredBinarySensor(NavimowEntity, BinarySensorEntity):
    """On while the mower intends to cross a configured zone-pair gate."""

    _attr_icon = "mdi:gate-alert"

    def __init__(self, coordinator: NavimowCoordinator, gate: NavimowerGate) -> None:
        super().__init__(coordinator, f"gate_{gate.slug}_required")
        self.gate = gate
        self._attr_name = f"{gate.name} required"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.gate_state(self.gate)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.gate_state(self.gate) is not None

    @property
    def extra_state_attributes(self) -> dict:
        return self.coordinator.gate_attributes(self.gate)
