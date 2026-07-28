"""Authenticated HTTP endpoint for the Navimower custom map card."""
from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_REGISTERED_KEY = f"{DOMAIN}_map_api_registered"


class NavimowerMapView(HomeAssistantView):
    """Return static private-cloud map geometry without Recorder state bloat."""

    url = "/api/navimower/map/{entry_id}"
    name = "api:navimower:map"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        coordinator = (hass.data.get(DOMAIN) or {}).get(entry_id)
        if coordinator is None:
            raise web.HTTPNotFound(text="Unknown Navimower config entry")
        return self.json(coordinator.map_payload())


def async_register_map_api(hass: HomeAssistant) -> None:
    """Register the map endpoint once per Home Assistant process."""
    if hass.data.get(_REGISTERED_KEY):
        return
    hass.http.register_view(NavimowerMapView())
    hass.data[_REGISTERED_KEY] = True
