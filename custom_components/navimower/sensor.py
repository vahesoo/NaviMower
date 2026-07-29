"""Sensor platform for Navimower."""
from __future__ import annotations

from collections.abc import Callable
import math
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfArea, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NavimowCoordinator
from .entity import NavimowEntity


@dataclass(frozen=True, kw_only=True)
class NavimowSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor over the snapshot dict."""

    value_fn: Callable[[dict], Any]
    attrs_fn: Callable[[dict], dict | None] | None = None


def _schedule_summary(schedule: list | None) -> str | None:
    """Short human summary of the weekly schedule for the sensor state."""
    if not schedule:
        return None
    days = [d.get("weekday") for d in schedule if d.get("enabled") and d.get("periods")]
    return ", ".join(d for d in days if d) if days else "Off"


SENSORS: tuple[NavimowSensorDescription, ...] = (
    NavimowSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda d: d.get("battery"),
    ),
    NavimowSensorDescription(
        key="state",
        translation_key="state",
        icon="mdi:robot-mower",
        value_fn=lambda d: d.get("state"),
    ),
    NavimowSensorDescription(
        key="state_code",
        translation_key="state_code",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("state_code"),
    ),
    NavimowSensorDescription(
        key="mowing_progress",
        translation_key="mowing_progress",
        icon="mdi:progress-check",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("mowing_progress"),
    ),
    NavimowSensorDescription(
        key="position_x",
        name="Position X",
        icon="mdi:axis-x-arrow",
        native_unit_of_measurement="m",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (d.get("position") or {}).get("x"),
        attrs_fn=lambda d: {"source": d.get("pose_source"), "pose_time": d.get("pose_time")},
    ),
    NavimowSensorDescription(
        key="position_y",
        name="Position Y",
        icon="mdi:axis-y-arrow",
        native_unit_of_measurement="m",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (d.get("position") or {}).get("y"),
        attrs_fn=lambda d: {"source": d.get("pose_source"), "pose_time": d.get("pose_time")},
    ),
    NavimowSensorDescription(
        key="heading",
        name="Heading",
        icon="mdi:compass",
        native_unit_of_measurement="°",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (
            round(math.degrees(float((d.get("position") or {}).get("heading"))) % 360, 1)
            if (d.get("position") or {}).get("heading") is not None
            else None
        ),
        attrs_fn=lambda d: {"source": d.get("pose_source"), "pose_time": d.get("pose_time")},
    ),
    NavimowSensorDescription(
        key="pose_age",
        name="MQTT pose age",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: round(d.get("mqtt_pose_age"), 1) if d.get("mqtt_pose_age") is not None else None,
    ),
    NavimowSensorDescription(
        key="mow_route_progress",
        name="Mow route progress",
        icon="mdi:progress-check",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("mow_route_progress"),
    ),
    NavimowSensorDescription(
        key="current_zone",
        translation_key="current_zone",
        icon="mdi:select-marker",
        value_fn=lambda d: d.get("current_zone"),
        attrs_fn=lambda d: {
            "zone_ids": d.get("current_zone_ids"),
            "meaning": "private_cloud_partition_selection",
        },
    ),
    NavimowSensorDescription(
        key="current_physical_zone",
        translation_key="current_physical_zone",
        icon="mdi:map-marker-radius",
        value_fn=lambda d: d.get("current_physical_zone"),
        attrs_fn=lambda d: {
            "zone_id": d.get("current_physical_zone_id"),
            "source": d.get("current_physical_zone_source"),
            "pose_age": d.get("mqtt_pose_age"),
        },
    ),
    NavimowSensorDescription(
        key="target_zone",
        translation_key="target_zone",
        icon="mdi:map-marker-path",
        value_fn=lambda d: d.get("target_zone"),
        attrs_fn=lambda d: {
            "zone_ids": d.get("target_zone_ids"),
            "dock_zone_id": d.get("dock_zone_id"),
        },
    ),
    NavimowSensorDescription(
        key="current_tunnel",
        translation_key="current_tunnel",
        icon="mdi:tunnel",
        value_fn=lambda d: d.get("current_tunnel"),
        attrs_fn=lambda d: {
            "tunnel_id": d.get("current_tunnel_id"),
            "connection": d.get("current_tunnel_connection"),
            "distance_m": d.get("current_tunnel_distance"),
        },
    ),
    NavimowSensorDescription(
        key="coverage",
        translation_key="coverage",
        icon="mdi:grid",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        # Overall mowed % of the current/last session (per-zone in attributes).
        value_fn=lambda d: (d.get("coverage") or {}).get("overall_pct"),
        attrs_fn=lambda d: (
            {
                "total_area": (d.get("coverage") or {}).get("total_area"),
                "finished_area": (d.get("coverage") or {}).get("finished_area"),
                "zones": [
                    {
                        "name": z.get("name"),
                        "percentage": z.get("pct"),
                        "finished_area": z.get("finished"),
                        "area": z.get("area"),
                    }
                    for z in (d.get("coverage") or {}).get("zones") or []
                ],
            }
            if d.get("coverage")
            else None
        ),
    ),
    NavimowSensorDescription(
        key="session_area",
        translation_key="session_area",
        icon="mdi:texture-box",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("session_area"),
    ),
    NavimowSensorDescription(
        key="weekly_area",
        translation_key="weekly_area",
        icon="mdi:calendar-week",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.get("weekly_area"),
    ),
    NavimowSensorDescription(
        key="total_area",
        translation_key="total_area",
        icon="mdi:ruler-square",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("total_area"),
    ),
    NavimowSensorDescription(
        key="next_mow",
        translation_key="next_mow",
        icon="mdi:clock-start",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("next_mow"),
    ),
    NavimowSensorDescription(
        key="schedule",
        translation_key="schedule",
        icon="mdi:calendar-clock",
        value_fn=lambda d: _schedule_summary(d.get("schedule")),
        # `days` = parsed weekly plan; `zones` = available zones (id/name) so the
        # graphical scheduler card can offer a per-period zone picker.
        attrs_fn=lambda d: {
            "days": d.get("schedule"),
            "zones": [
                {"id": z.get("id"), "name": z.get("name")}
                for z in (d.get("zones") or [])
                if z.get("id") is not None
            ],
        },
    ),
    NavimowSensorDescription(
        key="error_text",
        translation_key="error_text",
        icon="mdi:alert-circle",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("error_text"),
    ),
    NavimowSensorDescription(
        key="signal_wifi",
        translation_key="signal_wifi",
        icon="mdi:wifi",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("signal_wifi"),
    ),
    NavimowSensorDescription(
        key="blades_life",
        translation_key="blades_life",
        icon="mdi:saw-blade",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("maintenance") or {}).get("blades_pct"),
        attrs_fn=lambda d: {
            "reminder_interval_hours": (d.get("maintenance") or {}).get("blades_set_hours"),
            "runtime_minutes": (d.get("maintenance") or {}).get("blades_used_min"),
        },
    ),
    NavimowSensorDescription(
        key="chassis_life",
        translation_key="chassis_life",
        icon="mdi:car-wrench",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.get("maintenance") or {}).get("chassis_pct"),
        attrs_fn=lambda d: {
            "reminder_interval_hours": (d.get("maintenance") or {}).get("chassis_set_hours"),
            "runtime_minutes": (d.get("maintenance") or {}).get("chassis_used_min"),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: NavimowCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [NavimowSensor(coordinator, desc) for desc in SENSORS]
    entities.append(NavimowerMapDataSensor(coordinator, entry.entry_id))
    async_add_entities(entities)


class NavimowSensor(NavimowEntity, SensorEntity):
    """A single value from the mower snapshot."""

    entity_description: NavimowSensorDescription

    def __init__(
        self, coordinator: NavimowCoordinator, description: NavimowSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.data)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.data)


class NavimowerMapDataSensor(NavimowEntity, SensorEntity):
    """Small map metadata entity pointing the card to the authenticated API."""

    _attr_has_entity_name = True
    _attr_name = "Map data"
    _attr_icon = "mdi:map"

    def __init__(self, coordinator: NavimowCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, "map_data")
        self._entry_id = entry_id

    @property
    def native_value(self) -> str:
        map_data = self.data.get("map") or {}
        return "loaded" if map_data else "unavailable"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        map_data = self.data.get("map") or {}
        return {
            "api_path": f"/api/navimower/map/{self._entry_id}",
            "entry_id": self._entry_id,
            "area": map_data.get("area"),
            "zone_count": len(map_data.get("zones") or []),
            "channel_count": len(self.coordinator.channels),
            "map_version": map_data.get("version"),
            "map_modified_count": map_data.get("modified_count"),
            "trail_session": self.coordinator.trail_session,
            "trail_points": len(self.data.get("trail") or []),
            "trail_active": bool(self.data.get("trail_active")),
            "activity": self.data.get("activity"),
            "current_physical_zone": self.data.get("current_physical_zone"),
            "target_zone": self.data.get("target_zone"),
            "current_tunnel": self.data.get("current_tunnel"),
        }
