"""Authenticated HTTP API for the standalone Navimower Map Card."""
from __future__ import annotations

from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MAP_API_SCHEMA_VERSION

_REGISTERED_KEY = f"{DOMAIN}_map_api_registered"
_FALSE_QUERY_VALUES = frozenset({"0", "false", "no", "off"})


def _coordinator(request: web.Request, entry_id: str):
    hass: HomeAssistant = request.app["hass"]
    coordinator = (hass.data.get(DOMAIN) or {}).get(entry_id)
    if coordinator is None:
        raise web.HTTPNotFound(text="Unknown Navimower config entry")
    return coordinator


def _query_enabled(request: web.Request, key: str) -> bool:
    """Return an optional include flag, preserving the full legacy response."""
    value = request.query.get(key)
    if value is None:
        return True
    return str(value).strip().lower() not in _FALSE_QUERY_VALUES


async def _async_map_payload(
    coordinator: Any,
    *,
    include_sessions: bool,
    include_daily_trails: bool,
) -> dict[str, Any]:
    """Build only the map payload sections requested by the frontend.

    Older cards omit both query parameters and keep the original complete
    response. Newer cards can skip retained session points and daily trail
    geometry, avoiding their storage reads, simplification work, JSON encoding,
    transfer, and browser parsing on every dashboard load.
    """
    if include_sessions and include_daily_trails:
        return await coordinator.async_map_payload()

    sessions = (
        await coordinator.history.async_card_sessions() if include_sessions else []
    )
    daily_trails = None
    if include_daily_trails:
        data = coordinator.data or {}
        map_data = data.get("map") or coordinator._map_snapshot(
            coordinator._map_geometry or {},
            cutting_height_supported=data.get("cutting_height_supported"),
        )
        today = dt_util.now().date().isoformat()
        daily_cache_key = (
            today,
            coordinator.history.trail_revision,
            coordinator._map_cache_key,
        )
        if (
            coordinator._daily_trails_cache_key == daily_cache_key
            and coordinator._daily_trails_cache is not None
        ):
            daily_trails = coordinator._daily_trails_cache
        else:
            daily_trails = await coordinator.history.async_daily_zone_trails(
                [
                    dict(item)
                    for item in (map_data or {}).get("zones") or []
                    if isinstance(item, dict)
                ]
            )
            coordinator._daily_trails_cache_key = daily_cache_key
            coordinator._daily_trails_cache = daily_trails

    payload = coordinator._map_payload_with_sessions(sessions, daily_trails)
    if not include_sessions:
        for key in (
            "sessions",
            "session_xy_point_format",
            "session_segment_point_format",
        ):
            payload.pop(key, None)
    if not include_daily_trails:
        payload.pop("daily_trails", None)
        payload.pop("daily_trails_revision", None)
    return payload


class NavimowerMapView(HomeAssistantView):
    """Return map geometry, live trail and optional retained card history."""

    url = "/api/navimower/map/{entry_id}"
    name = "api:navimower:map"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        coordinator = _coordinator(request, entry_id)
        return self.json(
            await _async_map_payload(
                coordinator,
                include_sessions=_query_enabled(request, "include_sessions"),
                include_daily_trails=_query_enabled(
                    request, "include_daily_trails"
                ),
            )
        )


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
                "session_render_api_path_template": (
                    f"/api/navimower/session-render/{entry_id}/{{session_id}}"
                ),
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


class NavimowerSessionRenderView(HomeAssistantView):
    """Return one compact SVG-ready render archive for a completed session."""

    url = "/api/navimower/session-render/{entry_id}/{session_id}"
    name = "api:navimower:session-render"
    requires_auth = True

    async def get(
        self,
        request: web.Request,
        entry_id: str,
        session_id: str,
    ) -> web.Response:
        coordinator = _coordinator(request, entry_id)
        manager = getattr(coordinator, "session_archive", None)
        if manager is None:
            raise web.HTTPServiceUnavailable(
                text="Navimower session archive manager is unavailable"
            )
        render = await manager.async_get(session_id)
        if render is None:
            raise web.HTTPNotFound(
                text="No completed Navimower session render is available"
            )
        return self.json(
            {
                "schema_version": MAP_API_SCHEMA_VERSION,
                "render_schema_version": render.get("version"),
                "entry_id": entry_id,
                "session_id": session_id,
                "render": render,
            }
        )


def async_register_map_api(hass: HomeAssistant) -> None:
    """Register all map/history endpoints once per Home Assistant process."""
    if hass.data.get(_REGISTERED_KEY):
        return
    hass.http.register_view(NavimowerMapView())
    hass.http.register_view(NavimowerSessionsView())
    hass.http.register_view(NavimowerSessionView())
    hass.http.register_view(NavimowerSessionRenderView())
    hass.data[_REGISTERED_KEY] = True
