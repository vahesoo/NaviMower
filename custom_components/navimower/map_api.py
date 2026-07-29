"""Authenticated HTTP API for the standalone Navimower Map Card."""
from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN, MAP_API_SCHEMA_VERSION

_REGISTERED_KEY = f"{DOMAIN}_map_api_registered"


def _coordinator(request: web.Request, entry_id: str):
    hass: HomeAssistant = request.app["hass"]
    coordinator = (hass.data.get(DOMAIN) or {}).get(entry_id)
    if coordinator is None:
        raise web.HTTPNotFound(text="Unknown Navimower config entry")
    return coordinator


class NavimowerMapView(HomeAssistantView):
    """Return map geometry, live trail and retained card sessions."""

    url = "/api/navimower/map/{entry_id}"
    name = "api:navimower:map"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        coordinator = _coordinator(request, entry_id)
        return self.json(await coordinator.async_map_payload())


class NavimowerSessionsView(HomeAssistantView):
    """Return lightweight retained-session metadata."""

    url = "/api/navimower/sessions/{entry_id}"
    name = "api:navimower:sessions"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        coordinator = _coordinator(request, entry_id)
        payload = coordinator.sessions_payload()
        return self.json(
            {
                "schema_version": MAP_API_SCHEMA_VERSION,
                **payload,
            }
        )


class NavimowerSessionView(HomeAssistantView):
    """Return one complete timestamped session."""

    url = "/api/navimower/session/{entry_id}/{session_id}"
    name = "api:navimower:session"
    requires_auth = True

    async def get(
        self,
        request: web.Request,
        entry_id: str,
        session_id: str,
    ) -> web.Response:
        coordinator = _coordinator(request, entry_id)
        payload = await coordinator.async_session_payload(session_id)
        if payload is None:
            raise web.HTTPNotFound(text="Unknown Navimower session")
        return self.json(
            {
                "schema_version": MAP_API_SCHEMA_VERSION,
                "entry_id": entry_id,
                "session": payload,
            }
        )


def async_register_map_api(hass: HomeAssistant) -> None:
    """Register all map/history endpoints once per Home Assistant process."""
    if hass.data.get(_REGISTERED_KEY):
        return
    hass.http.register_view(NavimowerMapView())
    hass.http.register_view(NavimowerSessionsView())
    hass.http.register_view(NavimowerSessionView())
    hass.data[_REGISTERED_KEY] = True
