"""Sensor platform for Navimower."""
from __future__ import annotations

from collections.abc import Callable
import math
from dataclasses import dataclass
from typing import Any

from homeassistant.util import dt as dt_util

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfArea,
    UnitOfLength,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MAP_API_SCHEMA_VERSION
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


def _vendor_map_mowed_area(data: dict) -> float | None:
    """Return the vendor coverage snapshot's summed finished area."""
    coverage = data.get("coverage") or {}
    try:
        value = float(coverage.get("finished_area"))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _vendor_map_coverage(data: dict) -> float | None:
    """Return coverage derived only from the current vendor area snapshot."""
    coverage = data.get("coverage") or {}
    try:
        area = float(coverage.get("total_area"))
        finished = float(coverage.get("finished_area"))
    except (TypeError, ValueError):
        return None
    if area <= 0 or finished < 0:
        return None
    return round(max(0.0, min(100.0, 100.0 * finished / area)), 1)


SENSORS: tuple[NavimowSensorDescription, ...] = (
    NavimowSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda d: d.get("battery"),
        attrs_fn=lambda d: {
            "source": d.get("battery_source"),
            "source_age": d.get("battery_source_age"),
            "mqtt_battery": d.get("battery_mqtt"),
            "mqtt_age": d.get("battery_mqtt_age"),
            "private_cloud_battery": d.get("battery_private_cloud"),
        },
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
        key="task_progress",
        name="Task progress",
        icon="mdi:progress-check",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("mowing_progress"),
        attrs_fn=lambda d: {
            "task_zone_ids": (d.get("totals") or {}).get("task_zone_ids"),
            "task_area_m2": (d.get("totals") or {}).get("task_area_m2"),
            "task_mowed_area_m2": (d.get("totals") or {}).get("task_mowed_area_m2"),
            "active_zone_id": (d.get("totals") or {}).get("active_zone_id"),
            "cycle_id": d.get("active_cycle_id"),
            "source": d.get("mowing_progress_source"),
            "source_age": d.get("mowing_progress_source_age"),
            "mqtt_task_percentage": (d.get("mowing_progress_mqtt") or {}).get("mowing_percentage"),
            "private_cloud_task_percentage": d.get("task_progress_private_cloud"),
            "zone_weighted_fallback_pct": (d.get("totals") or {}).get(
                "task_zone_progress_weighted_pct"
            ),
            "meaning": "vendor_overall_selected_task_progress_raw_first",
        },
    ),
    NavimowSensorDescription(
        key="cutting_height",
        translation_key="cutting_height",
        icon="mdi:arrow-expand-vertical",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (d.get("settings") or {}).get("cut_height"),
        attrs_fn=lambda d: {
            "active_cutting_height_mm": d.get("active_cutting_height_mm"),
            "zone_detail_count": len(d.get("zone_details") or []),
        },
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
        value_fn=lambda d: (
            round(d.get("mqtt_pose_age"), 1)
            if d.get("mqtt_pose_age") is not None
            else None
        ),
    ),
    NavimowSensorDescription(
        key="position_source",
        translation_key="position_source",
        icon="mdi:source-branch",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("pose_source"),
        attrs_fn=lambda d: {
            "mqtt_pose_valid": d.get("mqtt_pose_valid"),
            "mqtt_pose_age": d.get("mqtt_pose_age"),
            "private_poll_age": d.get("private_poll_age"),
            "private_poll_profile": d.get("private_poll_profile"),
        },
    ),
    NavimowSensorDescription(
        key="mqtt_stream_state",
        translation_key="mqtt_stream_state",
        icon="mdi:access-point-network",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("mqtt_stream_state"),
        attrs_fn=lambda d: {
            "stream_expected": d.get("mqtt_stream_expected"),
            "recovery_count": d.get("mqtt_recovery_count"),
            "last_recovery_reason": d.get("mqtt_last_recovery_reason"),
            "last_location_message_age": d.get(
                "mqtt_last_location_message_age"
            ),
        },
    ),
    NavimowSensorDescription(
        key="private_poll_age",
        translation_key="private_poll_age",
        icon="mdi:cloud-clock",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (
            round(d.get("private_poll_age"), 1)
            if d.get("private_poll_age") is not None
            else None
        ),
        attrs_fn=lambda d: {
            "core_age": d.get("private_core_age"),
            "profile": d.get("private_poll_profile"),
        },
    ),
    NavimowSensorDescription(
        key="route_progress",
        name="Route progress",
        icon="mdi:routes",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("mow_route_progress"),
        attrs_fn=lambda d: {
            "meaning": "vendor_planned_route_progress",
            "not_area_coverage": True,
        },
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
            "source": d.get("target_zone_source"),
            "command_source": d.get("target_zone_command_source"),
            "target_age_seconds": d.get("target_zone_age_seconds"),
            "command_target_active": d.get("command_target_active"),
        },
    ),
    NavimowSensorDescription(
        key="current_channel",
        translation_key="current_channel",
        icon="mdi:tunnel",
        value_fn=lambda d: d.get("current_channel"),
        attrs_fn=lambda d: {
            "channel_id": d.get("current_channel_id"),
            "tunnel_id": d.get("current_channel_id"),
            "connection": d.get("current_channel_connection"),
            "distance_m": d.get("current_channel_distance"),
            "source": d.get("current_channel_source"),
            "stale": d.get("current_channel_stale"),
            "pose_valid": d.get("current_channel_pose_valid"),
            "pose_age": d.get("current_channel_pose_age"),
        },
    ),
    NavimowSensorDescription(
        key="map_coverage",
        name="Map coverage",
        icon="mdi:grid",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _vendor_map_coverage(d),
        attrs_fn=lambda d: {
            "map_area_m2": (d.get("coverage") or {}).get("total_area"),
            "map_mowed_area_m2": _vendor_map_mowed_area(d),
            "source": "private_cloud_coverage",
            "calculation": "vendor_finished_area_over_vendor_area",
            "interpreted_zone_model_pct": (d.get("totals") or {}).get("map_coverage_pct"),
            "zone_count": (d.get("totals") or {}).get("zone_count"),
            "completed_zone_count": (d.get("totals") or {}).get("completed_zone_count"),
            "zone_states_revision": d.get("zone_states_revision"),
        },
    ),
    NavimowSensorDescription(
        key="task_mowed_area",
        name="Task mowed area",
        icon="mdi:texture-box",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("session_area"),
        attrs_fn=lambda d: {
            "task_area_m2": (d.get("totals") or {}).get("task_area_m2"),
            "task_progress_pct": (d.get("totals") or {}).get("task_progress_pct"),
            "task_zone_ids": (d.get("totals") or {}).get("task_zone_ids"),
            "source": d.get("session_area_source"),
            "source_age": d.get("session_area_source_age"),
            "mqtt_subtotal_area_m2": d.get("session_area_mqtt"),
            "private_cloud_subtotal_area_m2": d.get("session_area_private_cloud"),
            "interpreted_task_mowed_area_m2": (d.get("totals") or {}).get("task_mowed_area_m2"),
        },
    ),
    NavimowSensorDescription(
        key="map_mowed_area",
        name="Map mowed area",
        icon="mdi:map-check",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _vendor_map_mowed_area(d),
        attrs_fn=lambda d: {
            "map_area_m2": (d.get("coverage") or {}).get("total_area"),
            "map_coverage_pct": _vendor_map_coverage(d),
            "source": "private_cloud_coverage",
            "interpreted_zone_model_mowed_area_m2": (d.get("totals") or {}).get("map_mowed_area_m2"),
        },
    ),
    NavimowSensorDescription(
        key="weekly_mowed_area",
        name="Weekly mowed area",
        icon="mdi:calendar-week",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.get("weekly_area"),
    ),
    NavimowSensorDescription(
        key="map_area",
        name="Map area",
        icon="mdi:ruler-square",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (d.get("totals") or {}).get("map_area_m2"),
        attrs_fn=lambda d: {
            "zone_count": (d.get("totals") or {}).get("zone_count"),
            "source": "decoded_map_zones",
        },
    ),
    NavimowSensorDescription(
        key="last_map_mowed",
        name="Last map mowed",
        icon="mdi:clock-check-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=False,
        value_fn=lambda d: dt_util.parse_datetime(d.get("last_map_mowed_at"))
        if d.get("last_map_mowed_at")
        else None,
    ),
    NavimowSensorDescription(
        key="last_map_completed",
        name="Last map completed",
        icon="mdi:map-check-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=False,
        value_fn=lambda d: dt_util.parse_datetime(d.get("last_map_completed_at"))
        if d.get("last_map_completed_at")
        else None,
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


@dataclass(frozen=True, kw_only=True)
class ZoneMetricDescription:
    key: str
    label: str
    icon: str
    value_key: str
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    enabled_default: bool = False


ZONE_METRICS: tuple[ZoneMetricDescription, ...] = (
    ZoneMetricDescription(
        key="coverage",
        label="coverage",
        icon="mdi:progress-check",
        value_key="coverage_pct",
        unit=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        enabled_default=True,
    ),
    ZoneMetricDescription(
        key="area",
        label="area",
        icon="mdi:ruler-square",
        value_key="area_m2",
        unit=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ZoneMetricDescription(
        key="mowed_area",
        label="mowed area",
        icon="mdi:map-check",
        value_key="mowed_area_m2",
        unit=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ZoneMetricDescription(
        key="last_mowed",
        label="last mowed",
        icon="mdi:clock-outline",
        value_key="last_mowed_at",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    ZoneMetricDescription(
        key="last_completed",
        label="last completed",
        icon="mdi:check-circle-outline",
        value_key="last_completed_at",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
)


class NavimowerZoneSensor(NavimowEntity, SensorEntity):
    """One zone metric backed by the central integration zone model."""

    def __init__(
        self,
        coordinator: NavimowCoordinator,
        zone_id: int,
        metric: ZoneMetricDescription,
    ) -> None:
        super().__init__(coordinator, f"zone_{zone_id}_{metric.key}")
        self._zone_id = zone_id
        self._metric = metric
        self._attr_icon = metric.icon
        self._attr_native_unit_of_measurement = metric.unit
        self._attr_device_class = metric.device_class
        self._attr_state_class = metric.state_class
        self._attr_entity_registry_enabled_default = metric.enabled_default

    @property
    def name(self) -> str:
        """Follow zone renames without changing the stable unique ID."""
        return f"{self._zone_name()} {self._metric.label}"

    def _row(self) -> dict[str, Any]:
        return next(
            (
                row
                for row in self.data.get("zone_states") or []
                if str(row.get("id")) == str(self._zone_id)
            ),
            {},
        )

    def _zone_name(self) -> str:
        row = self._row()
        return str(row.get("name") or f"Zone {self._zone_id}")

    @property
    def native_value(self) -> Any:
        value = self._row().get(self._metric.value_key)
        if self._metric.device_class == SensorDeviceClass.TIMESTAMP:
            return dt_util.parse_datetime(value) if value else None
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        row = dict(self._row())
        row.pop(self._metric.value_key, None)
        row["zone_id"] = self._zone_id
        row["zone_name"] = self._zone_name()
        row["zone_states_revision"] = self.data.get("zone_states_revision")
        return row


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: NavimowCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [NavimowSensor(coordinator, desc) for desc in SENSORS]
    entities.append(NavimowerMapDataSensor(coordinator, entry.entry_id))
    async_add_entities(entities)

    known_zone_ids: set[int] = set()

    def _add_new_zone_entities() -> None:
        new_entities: list[SensorEntity] = []
        for row in coordinator.data.get("zone_states") or []:
            try:
                zone_id = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            if zone_id in known_zone_ids:
                continue
            known_zone_ids.add(zone_id)
            new_entities.extend(
                NavimowerZoneSensor(coordinator, zone_id, metric)
                for metric in ZONE_METRICS
            )
        if new_entities:
            async_add_entities(new_entities)

    _add_new_zone_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_zone_entities))


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
        # The session index contains metadata only. Avoid deep-copying active and
        # cached full sessions (including thousands of route points) on every
        # Home Assistant state write.
        session_index = self.coordinator.history.sessions_index_payload()
        return {
            "schema_version": MAP_API_SCHEMA_VERSION,
            "api_path": f"/api/navimower/map/{self._entry_id}",
            "sessions_api_path": f"/api/navimower/sessions/{self._entry_id}",
            "session_api_path_template": (
                f"/api/navimower/session/{self._entry_id}/{{session_id}}"
            ),
            "entry_id": self._entry_id,
            "area": map_data.get("area"),
            "zone_count": len(map_data.get("zones") or []),
            "doodle_count": len(map_data.get("doodles") or []),
            "channel_count": len(self.coordinator.channels),
            "gate_count": len(self.coordinator.gates),
            "map_version": map_data.get("revision") or map_data.get("version"),
            "map_revision": map_data.get("revision"),
            "map_edit_time": map_data.get("edit_time"),
            "map_modified_count": map_data.get("modified_count"),
            "cut_height": (self.data.get("settings") or {}).get("cut_height"),
            "zone_detail_count": len(self.data.get("zone_details") or []),
            "zone_state_count": len(self.data.get("zone_states") or []),
            "zone_states_revision": self.data.get("zone_states_revision"),
            "daily_trails_revision": self.coordinator.history.trail_revision,
            "map_area_m2": (self.data.get("totals") or {}).get("map_area_m2"),
            "map_coverage_pct": (self.data.get("totals") or {}).get("map_coverage_pct"),
            "task_progress_pct": (self.data.get("totals") or {}).get("task_progress_pct"),
            "trail_session": self.coordinator.trail_session,
            "trail_started_at": self.coordinator.history.active_started_at(),
            "trail_points": len(self.data.get("trail") or []),
            "trail_active": bool(self.data.get("trail_active")),
            "active_session_id": session_index.get("active_session_id"),
            "retained_session_count": len(session_index.get("sessions") or []),
            "trail_retention_days": self.coordinator.history.retention_days,
            "include_return_trail": self.coordinator.history.include_return_trail,
            "private_cloud_connected": self.data.get("private_cloud_connected"),
            "oauth_configured": self.data.get("oauth_configured"),
            "oauth_connected": self.data.get("oauth_connected"),
            "mqtt_connected": self.data.get("mqtt_connected"),
            "mqtt_pose_valid": self.data.get("mqtt_pose_valid"),
            "mqtt_stream_state": self.data.get("mqtt_stream_state"),
            "mqtt_recovery_count": self.data.get("mqtt_recovery_count"),
            "position_source": self.data.get("pose_source"),
            "private_poll_age": self.data.get("private_poll_age"),
            "private_poll_profile": self.data.get("private_poll_profile"),
            "activity": self.data.get("activity"),
            "current_physical_zone": self.data.get("current_physical_zone"),
            "target_zone": self.data.get("target_zone"),
            "current_channel": self.data.get("current_channel"),
        }
