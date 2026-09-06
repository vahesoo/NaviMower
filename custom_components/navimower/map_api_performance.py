"""Phased Map API responses for fast card-first rendering.

Legacy callers keep the complete response by default. New cards may omit the
backend current-cycle artifact from the first map request and fetch that compact
artifact independently, so map geometry and controls are never blocked by
history rendering.
"""
from __future__ import annotations

from typing import Any

from aiohttp import web
from homeassistant.util import dt as dt_util

from . import map_api as _map_api
from .const import MAP_API_SCHEMA_VERSION


def _query_requested(request: web.Request, key: str) -> bool:
    value = request.query.get(key)
    if value is None:
        return False
    return str(value).strip().lower() not in _map_api._FALSE_QUERY_VALUES  # noqa: SLF001


async def _async_map_payload(
    coordinator: Any,
    *,
    include_sessions: bool,
    include_daily_trails: bool,
    include_current_cycle: bool = True,
) -> dict[str, Any]:
    """Build only explicitly requested payload sections."""
    current_cycle_render = (
        await _map_api._async_current_cycle_render(coordinator)  # noqa: SLF001
        if include_current_cycle
        else None
    )

    if include_sessions and include_daily_trails:
        payload = await coordinator.async_map_payload()
    else:
        sessions = (
            await coordinator.history.async_card_sessions()
            if include_sessions
            else []
        )
        daily_trails = None
        if include_daily_trails:
            today = dt_util.now().date().isoformat()
            daily_cache_key = (
                today,
                coordinator.history.trail_revision,
                coordinator._map_cache_key,  # noqa: SLF001
            )
            if (
                coordinator._daily_trails_cache_key == daily_cache_key  # noqa: SLF001
                and coordinator._daily_trails_cache is not None  # noqa: SLF001
            ):
                daily_trails = coordinator._daily_trails_cache  # noqa: SLF001
            else:
                daily_trails = await coordinator.history.async_daily_zone_trails(
                    _map_api._map_zones(coordinator)  # noqa: SLF001
                )
                coordinator._daily_trails_cache_key = daily_cache_key  # noqa: SLF001
                coordinator._daily_trails_cache = daily_trails  # noqa: SLF001

        payload = coordinator._map_payload_with_sessions(  # noqa: SLF001
            sessions,
            daily_trails,
        )
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

    if current_cycle_render is not None:
        payload["current_cycle_render"] = current_cycle_render
    else:
        payload.pop("current_cycle_render", None)
    return _map_api._with_card_metadata(coordinator, payload)  # noqa: SLF001


async def _async_current_cycle_only(coordinator: Any) -> dict[str, Any]:
    render = await _map_api._async_current_cycle_render(coordinator)  # noqa: SLF001
    data = coordinator.data or {}
    map_data = data.get("map") or {}
    return {
        "schema_version": MAP_API_SCHEMA_VERSION,
        "entry_id": coordinator.entry.entry_id,
        "map_revision": map_data.get("revision"),
        "map_version": map_data.get("map_version"),
        "trail_session": coordinator.history.active_session_no,
        "current_cycle_render": render,
    }


def install_map_api_performance() -> None:
    """Install backward-compatible phased Map API query options."""
    cls = _map_api.NavimowerMapView
    if getattr(cls, "_phased_payload_installed", False):
        return

    async def get(
        self: Any,
        request: web.Request,
        entry_id: str,
    ) -> web.Response:
        coordinator = _map_api._coordinator(request, entry_id)  # noqa: SLF001
        if _query_requested(request, "current_cycle_only"):
            return self.json(await _async_current_cycle_only(coordinator))
        return self.json(
            await _async_map_payload(
                coordinator,
                include_sessions=_map_api._query_enabled(  # noqa: SLF001
                    request,
                    "include_sessions",
                ),
                include_daily_trails=_map_api._query_enabled(  # noqa: SLF001
                    request,
                    "include_daily_trails",
                ),
                include_current_cycle=_map_api._query_enabled(  # noqa: SLF001
                    request,
                    "include_current_cycle",
                ),
            )
        )

    _map_api._async_map_payload = _async_map_payload  # noqa: SLF001
    cls.get = get
    cls._phased_payload_installed = True
