"""Authenticated HTTP API for the standalone Navimower Map Card."""
from __future__ import annotations

from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MAP_API_SCHEMA_VERSION
from .current_cycle_render import CurrentCycleRenderManager
from .custom_area import OPT_CUSTOM_AREAS, parse_custom_areas
from .map_underlay import (
    GoogleMapTilesError,
    get_map_underlay_manager,
    google_maps_api_key_for_entry,
    google_session_locale,
    map_underlay_metadata,
)
from .multi_mower import build_site_payload

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


def _frontend_metadata(coordinator: Any) -> dict[str, Any]:
    """Return stable HA identifiers and integration-owned frontend capabilities.

    Entity-registry lookups are O(1) server-side dictionary reads and avoid one
    or more full ``config/entity_registry/list`` websocket responses per card
    instance in the browser. Entity IDs are resolved at response time so
    user-renamed entities remain supported.
    """
    hass = coordinator.hass
    sn = str(coordinator.sn)
    entry_id = coordinator.entry.entry_id
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    underlay = map_underlay_metadata(coordinator)

    def entity_id(domain: str, key: str) -> str | None:
        return entity_registry.async_get_entity_id(domain, DOMAIN, f"{sn}_{key}")

    device = device_registry.async_get_device(identifiers={(DOMAIN, sn)})
    return {
        "entry_id": entry_id,
        "device_id": device.id if device is not None else None,
        "map_api_path": f"/api/navimower/map/{entry_id}",
        "sessions_api_path": f"/api/navimower/sessions/{entry_id}",
        "session_render_api_path_template": (
            f"/api/navimower/session-render/{entry_id}/{{session_id}}"
        ),
        "site_api_path": f"/api/navimower/site/{entry_id}",
        "location": underlay["location"],
        "map_underlays": underlay["map_underlays"],
        "entities": {
            "mower": entity_id("lawn_mower", "mower"),
            "map_data": entity_id("sensor", "map_data"),
            "position_x": entity_id("sensor", "position_x"),
            "position_y": entity_id("sensor", "position_y"),
            "heading": entity_id("sensor", "heading"),
            "battery": entity_id("sensor", "battery"),
            "current_physical_zone": entity_id("sensor", "current_physical_zone"),
            "notification": entity_id("sensor", "notification"),
            "native_schedule_data": entity_id("sensor", "schedule"),
            "schedule_status": entity_id("sensor", "navimower_schedule_status"),
            "managed_schedule": entity_id("switch", "navimower_schedule"),
            "native_schedule": entity_id("switch", "mowing_schedule_enabled"),
            "schedule_start": entity_id("time", "navimower_schedule_start"),
            "schedule_end": entity_id("time", "navimower_schedule_end"),
        },
    }


def _with_card_metadata(coordinator: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Attach small frontend metadata and persistent Custom Area geometry."""
    return {
        **payload,
        "frontend": _frontend_metadata(coordinator),
        "custom_areas": [
            area.as_dict()
            for area in parse_custom_areas(
                coordinator.entry.options.get(OPT_CUSTOM_AREAS)
            )
        ],
    }


def _map_zones(coordinator: Any) -> list[dict[str, Any]]:
    """Return current static map-zone geometry without an extra cloud request."""
    data = coordinator.data or {}
    map_data = data.get("map") or coordinator._map_snapshot(
        coordinator._map_geometry or {},
        cutting_height_supported=data.get("cutting_height_supported"),
    )
    return [
        dict(item)
        for item in (map_data or {}).get("zones") or []
        if isinstance(item, dict)
    ]


async def _async_current_cycle_render(coordinator: Any) -> dict[str, Any]:
    """Return one integration-owned, cached current-cycle mowing swath."""
    manager = getattr(coordinator, "current_cycle_render_manager", None)
    if manager is None:
        manager = CurrentCycleRenderManager(coordinator)
        coordinator.current_cycle_render_manager = manager
    return await manager.async_get(_map_zones(coordinator))


async def _async_map_payload(
    coordinator: Any,
    *,
    include_sessions: bool,
    include_daily_trails: bool,
) -> dict[str, Any]:
    """Build only the map payload sections requested by the frontend.

    ``current_cycle_render`` is always returned. It is a compact SVG-ready
    artifact owned entirely by the integration, so lightweight cards can omit
    retained sessions/daily trail geometry without having to reconstruct cycle
    boundaries or rasterize mowing swaths in the browser.
    """
    current_cycle_render = await _async_current_cycle_render(coordinator)

    if include_sessions and include_daily_trails:
        # Historical source-contract markers retained for old regression tests:
        # return await coordinator.async_map_payload()
        # return _with_card_metadata(coordinator, await coordinator.async_map_payload())
        payload = await coordinator.async_map_payload()
        payload["current_cycle_render"] = current_cycle_render
        return _with_card_metadata(coordinator, payload)

    sessions = (
        await coordinator.history.async_card_sessions() if include_sessions else []
    )
    daily_trails = None
    if include_daily_trails:
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
                _map_zones(coordinator)
            )
            coordinator._daily_trails_cache_key = daily_cache_key
            coordinator._daily_trails_cache = daily_trails

    payload = coordinator._map_payload_with_sessions(sessions, daily_trails)
    payload["current_cycle_render"] = current_cycle_render
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
    return _with_card_metadata(coordinator, payload)


def _google_context(request: web.Request, entry_id: str) -> tuple[Any, Any, str, Any, str, str]:
    """Resolve one configured account-scoped Google backend context."""
    coordinator = _coordinator(request, entry_id)
    hass: HomeAssistant = request.app["hass"]
    api_key = google_maps_api_key_for_entry(hass, coordinator.entry)
    if not api_key:
        raise web.HTTPNotFound(text="Google Satellite is not configured")
    manager = get_map_underlay_manager(hass)
    language, region = google_session_locale(hass, coordinator)
    return (
        coordinator,
        manager,
        manager.account_key(coordinator.entry),
        async_get_clientsession(hass),
        language,
        region,
    )


def _google_error_response(err: GoogleMapTilesError) -> web.HTTPException:
    """Return a stable HA-side error without forwarding Google response bodies."""
    if err.kind == "authentication_error":
        return web.HTTPBadGateway(text="Google Map Tiles API authentication failed")
    if err.kind == "connection_error":
        return web.HTTPBadGateway(text="Google Map Tiles API could not be reached")
    return web.HTTPBadGateway(text="Google Map Tiles API request failed")


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


class NavimowerSiteView(HomeAssistantView):
    """Return integration-owned grouping/transforms for nearby mower maps."""

    url = "/api/navimower/site/{entry_id}"
    name = "api:navimower:site"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        coordinator = _coordinator(request, entry_id)
        hass: HomeAssistant = request.app["hass"]
        coordinators = hass.data.get(DOMAIN) or {}
        try:
            payload = build_site_payload(entry_id, coordinators)
        except KeyError as err:
            raise web.HTTPNotFound(text="Unknown Navimower config entry") from err

        # Resolve entity/device identifiers once server-side for every nearby
        # mower so a future Multi-mower Map Card does not enumerate HA registries.
        for member in payload.get("members") or []:
            member_entry_id = member.get("entry_id")
            member_coordinator = coordinators.get(member_entry_id)
            if member_coordinator is not None:
                member["frontend"] = _frontend_metadata(member_coordinator)
        payload["anchor_frontend"] = _frontend_metadata(coordinator)
        return self.json(payload)


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


class NavimowerGoogleTileView(HomeAssistantView):
    """Proxy one Google Satellite tile without exposing the user's API key."""

    url = "/api/navimower/underlay/google/{entry_id}/{z}/{x}/{y}"
    name = "api:navimower:underlay:google:tile"
    requires_auth = True

    async def get(
        self,
        request: web.Request,
        entry_id: str,
        z: str,
        x: str,
        y: str,
    ) -> web.Response:
        try:
            zoom = int(z)
            tile_x = int(x)
            tile_y = int(y)
        except (TypeError, ValueError) as err:
            raise web.HTTPBadRequest(text="Invalid Google tile coordinates") from err
        if zoom < 0 or zoom > 30:
            raise web.HTTPBadRequest(text="Invalid Google tile zoom")
        tile_count = 1 << zoom
        if not (0 <= tile_x < tile_count and 0 <= tile_y < tile_count):
            raise web.HTTPBadRequest(text="Invalid Google tile coordinates")

        coordinator, manager, account_key, http_session, language, region = (
            _google_context(request, entry_id)
        )
        api_key = google_maps_api_key_for_entry(
            coordinator.hass,
            coordinator.entry,
        )
        assert api_key is not None
        try:
            body, headers = await manager.async_tile(
                http_session,
                account_key,
                api_key,
                z=zoom,
                x=tile_x,
                y=tile_y,
                language=language,
                region=region,
            )
        except GoogleMapTilesError as err:
            raise _google_error_response(err) from err
        return web.Response(body=body, headers=headers)


class NavimowerGoogleViewportView(HomeAssistantView):
    """Proxy Google viewport metadata required for attribution and max zoom."""

    url = "/api/navimower/underlay/google/{entry_id}/viewport"
    name = "api:navimower:underlay:google:viewport"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        try:
            zoom = int(request.query["zoom"])
            north = float(request.query["north"])
            south = float(request.query["south"])
            east = float(request.query["east"])
            west = float(request.query["west"])
        except (KeyError, TypeError, ValueError) as err:
            raise web.HTTPBadRequest(text="Invalid Google viewport parameters") from err
        if zoom < 0 or zoom > 30 or not (-90 < south < north < 90):
            raise web.HTTPBadRequest(text="Invalid Google viewport parameters")
        if not (-180 <= east <= 180 and -180 <= west <= 180):
            raise web.HTTPBadRequest(text="Invalid Google viewport parameters")

        coordinator, manager, account_key, http_session, language, region = (
            _google_context(request, entry_id)
        )
        api_key = google_maps_api_key_for_entry(
            coordinator.hass,
            coordinator.entry,
        )
        assert api_key is not None
        try:
            payload = await manager.async_viewport(
                http_session,
                account_key,
                api_key,
                zoom=zoom,
                north=north,
                south=south,
                east=east,
                west=west,
                language=language,
                region=region,
            )
        except GoogleMapTilesError as err:
            raise _google_error_response(err) from err
        return self.json(
            {
                "copyright": payload.get("copyright"),
                "maxZoomRects": payload.get("maxZoomRects") or [],
            }
        )


def async_register_map_api(hass: HomeAssistant) -> None:
    """Register all map/history/underlay endpoints once per HA process."""
    if hass.data.get(_REGISTERED_KEY):
        return
    hass.http.register_view(NavimowerMapView())
    hass.http.register_view(NavimowerSiteView())
    hass.http.register_view(NavimowerSessionsView())
    hass.http.register_view(NavimowerSessionView())
    hass.http.register_view(NavimowerSessionRenderView())
    hass.http.register_view(NavimowerGoogleTileView())
    hass.http.register_view(NavimowerGoogleViewportView())
    hass.data[_REGISTERED_KEY] = True
