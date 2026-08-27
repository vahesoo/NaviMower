"""Shared base entity for Navimower.

Ties every entity to the single mower device (keyed by vehicle_sn) and to the
coordinator's parsed snapshot.
"""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import NavimowCoordinator


# Canonical English display names for vendor settings whose historical entity
# keys no longer describe the app-facing feature precisely. Keep the entity key
# and unique ID stable; only the default Home Assistant display name changes.
ENTITY_NAME_OVERRIDES: dict[str, str] = {
    "frost_delay": "Frost detection",
    "frost_delay_until": "Frost delay",
    "high_temp_delay": "Max temp detection",
    "maximum_mowing_temperature": "Max temperature",
    "rain_detection": "Rain detection",
    "rain_sensor": "Rain sensor",
    "weather_rain": "Rain forecast",
    "weather_sensitivity": "Rain forecast sensitivity",
    "rain_delay_mode": "Rain delay",
    "rain_delay_time": "Rain delay duration",
    "snow_delay": "Snow detection",
    "snow_delay_time": "Snow delay",
    "storm_delay": "Wind detection",
    "do_not_disturb": "Do not disturb",
    "quiet_period_start": "Do not disturb start",
    "quiet_period_end": "Do not disturb end",
}


class NavimowEntity(CoordinatorEntity[NavimowCoordinator]):
    """Base class: all entities live under one HA device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NavimowCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._sn = coordinator.sn
        self._attr_unique_id = f"{self._sn}_{key}"
        if name := ENTITY_NAME_OVERRIDES.get(key):
            self._attr_name = name

    @property
    def data(self) -> dict:
        """The current parsed snapshot (may be empty before first refresh)."""
        return self.coordinator.data or {}

    @property
    def device_info(self) -> DeviceInfo:
        data = self.data
        return DeviceInfo(
            identifiers={(DOMAIN, self._sn)},
            manufacturer=MANUFACTURER,
            name=data.get("name") or "Navimow",
            model=data.get("model") or None,
            serial_number=self._sn,
        )

    @property
    def available(self) -> bool:
        # Available while the coordinator is succeeding; per-entity platforms
        # may further gate on whether their specific field is present.
        return super().available and bool(self.coordinator.data)
