"""Authenticated HTTP API for the standalone Navimower Map Card."""
from __future__ import annotations

from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MAP_API_SCHEMA_VERSION
from .current_cycle_render import CurrentCycleRenderManager
from .custom_area import OPT_CUSTOM_AREAS, parse_custom_areas

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
    """Return stable HA identifiers the Map Card otherwise has to rediscover.

    Entity-registry lookups are O(1) server-side dictionary reads and avoid one
    or more full ``config/entity_registry/list`` websocket responses per card
    instance in the browser. Entity IDs are resolved at response time so
    user-renamed entities remain supported.
    """
    hass = coordinator.hass
    sn = str(coordinator.sn)
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    def entity_id(domain: str, key: str) -> str | None:
        return entity_registry.async_get_entity_id(domain, DOMAIN, f"{sn}_{key}")

    device = device_registry.async_get_device(identifiers={(DOMAIN, sn)})
    return {
        "device_id": device.id if device is not None else None,
        "entities": {
            "mower": entity_id("lawn_mower", "mower"),
            "map_data": entity_id("sensor", "map_data"),
            "position_x": entity_id("sensor", "position_x"),
            "position_y": entity_id("sensor", "position_y"),
            "heading": entity_id("sensor", "heading"),
            "battery": entity_id("sensor", "battery"),
            "current_physical_zone": entity_id("sensor", "current_physical_zone"),
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
        # Historical full-payload shape retained semantically; beta56 only adds
        # current_cycle_render before frontend metadata is attached.
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
