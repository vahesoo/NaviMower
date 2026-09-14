"""Native Home Assistant GPS device tracker for Navimower."""
from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NavimowCoordinator
from .entity import NavimowEntity


def _coordinate(value: Any, *, minimum: float, maximum: float) -> float | None:
    """Return one finite coordinate inside the geographic range."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _latitude(data: dict[str, Any]) -> float | None:
    return _coordinate(data.get("latitude"), minimum=-90.0, maximum=90.0)


def _longitude(data: dict[str, Any]) -> float | None:
    return _coordinate(data.get("longitude"), minimum=-180.0, maximum=180.0)


def _coordinates(data: dict[str, Any]) -> tuple[float, float] | None:
    """Return a usable geographic fix, rejecting the vendor's no-fix 0/0 pair."""
    latitude = _latitude(data)
    longitude = _longitude(data)
    if latitude is None or longitude is None:
        return None
    if latitude == 0.0 and longitude == 0.0:
        return None
    return latitude, longitude


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the mower location tracker."""
    coordinator: NavimowCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([NavimowerDeviceTracker(coordinator)])


class NavimowerDeviceTracker(NavimowEntity, TrackerEntity):
    """Expose the mower's vendor-reported geographic position to Home Assistant."""

    # This is the mower's primary map marker, so use the device name directly
    # instead of displaying a separate "Location" suffix on the HA Map.
    _attr_name = None
    _attr_icon = "mdi:robot-mower"
    _attr_source_type = SourceType.GPS
    # A position tracker is a user-facing entity, not a diagnostic-only entity.
    _attr_entity_category = None
    # The account location response does not expose a trustworthy accuracy
    # radius, so do not invent one.
    _attr_location_accuracy = 0

    def __init__(self, coordinator: NavimowCoordinator) -> None:
        """Initialize one mower tracker on the existing mower HA device."""
        NavimowEntity.__init__(self, coordinator, "location")

    @property
    def force_update(self) -> bool:
        """Do not create recorder rows when a polled coordinate did not change."""
        return False

    @property
    def latitude(self) -> float | None:
        """Return the geographic latitude when a usable fix exists."""
        coordinates = _coordinates(self.data)
        return coordinates[0] if coordinates else None

    @property
    def longitude(self) -> float | None:
        """Return the geographic longitude when a usable fix exists."""
        coordinates = _coordinates(self.data)
        return coordinates[1] if coordinates else None

    @property
    def available(self) -> bool:
        """Only publish a map position when both coordinates are valid."""
        return (
            super().available
            and self.latitude is not None
            and self.longitude is not None
        )
