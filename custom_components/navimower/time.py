"""Legacy time platform retained for entity-registry compatibility.

The frost protection cutoff moved to a select entity in v0.3.4-beta7 so Home
Assistant can expose only the quarter-hour values accepted by the mower app.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Do not create legacy time entities."""
