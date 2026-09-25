"""Logbook descriptions for Navimower Activity contexts."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .activity_context import EVENT_NAVIMOWER_ACTIVITY
from .const import DOMAIN


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, Any]]], None],
) -> None:
    """Describe Navimower cause events for Home Assistant Activity."""

    @callback
    def _describe(event: Event) -> dict[str, Any]:
        data = event.data
        mower = str(data.get("mower_name") or "Navimow")
        name = str(data.get("name") or "Navimower")
        return {
            LOGBOOK_ENTRY_NAME: f"{mower} · {name}",
            LOGBOOK_ENTRY_MESSAGE: str(data.get("message") or "Navimower state changed."),
        }

    async_describe_event(DOMAIN, EVENT_NAVIMOWER_ACTIVITY, _describe)
