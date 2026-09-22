"""Phased Map API responses for fast card-first rendering.

Legacy callers keep the complete response by default. New cards may omit the
backend current-cycle artifact from the first map request and fetch that compact
artifact independently, so map geometry and controls are never blocked by
history rendering. Ready-only per-zone resources are an additive opt-in API.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from aiohttp import web
from homeassistant.util import dt as dt_util

from . import map_api as _map_api
from .const import MAP_API_SCHEMA_VERSION
from .map_artifacts import MapArtifactUnavailable


def _query_requested(request: web.Request, key: str) -> bool:
    value = request.query.get(key)
    if value is None:
        return False
    return str(value).strip().lower() not in _map_api._FALSE_QUERY_VALUES  # noqa: SLF001


async def _prepared_current_cycle_render(coordinator: Any) -> dict[str, Any]:
    manager = getattr(coordinator, "map_artifacts", None)
    if manager is None:
        return await _map_api._async_current_cycle_render(coordinator)  # noqa: SLF001
    try:
        return await manager.async_get(_map_api._map_zones(coordinator))  # noqa: SLF001
    except MapArtifactUnavailable as err:
        # Old cards retry a failed request, but can cache an empty successful one
        # indefinitely. Do not turn a cold/error cache into a false empty cycle.
        raise web.HTTPServiceUnavailable(
            text="Current-cycle render is being prepared",
            headers={"Retry-After": "30", "Cache-Control": "no-store"},
        ) from err


async def _async_map_payload(
    coordinator: Any,
    *,
    include_sessions: bool,
    include_daily_trails: bool,
    include_current_cycle: bool = True,
    include_prepared_live_tail: bool = False,
    prepared_live_tail_only: bool = False,
) -> dict[str, Any]:
    """Build only explicitly requested payload sections."""
    current_cycle_render = (
        await _prepared_current_cycle_render(coordinator)
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
    payload = _map_api._with_card_metadata(coordinator, payload)  # noqa: SLF001
    if getattr(coordinator, "map_artifacts", None) is not None:
        entry_id = quote(str(coordinator.entry.entry_id), safe="")
        payload["map_artifacts"] = {
            "schema_version": 1,
            "manifest_url": f"/api/navimower/map/{entry_id}?artifacts_only=1",
            "format": "svg", "ready_only": True,
        }
    prepared = getattr(coordinator, "prepared_render_model", None)
    if prepared is not None:
        payload["prepared_render_model"] = prepared.discovery()
        if include_prepared_live_tail or prepared_live_tail_only:
            live_tail = prepared.live_tail_payload(
                payload.get("trail_segments") or [],
                payload.get("trail_session"),
            )
            payload["prepared_live_tail"] = live_tail
            if prepared_live_tail_only and live_tail.get("usable"):
                # The prepared SVG resource owns the route backbone. A beta13+
                # frontend can opt into receiving only the tiny post-resource
                # live tail instead of serializing/transferring the full raw
                # flat + segmented trail on every Map API refresh.
                payload.pop("trail", None)
                payload.pop("trail_segments", None)
    return payload


async def _async_current_cycle_only(coordinator: Any) -> dict[str, Any]:
    render = await _prepared_current_cycle_render(coordinator)
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


def _zone_artifact_response(coordinator: Any, request: web.Request) -> web.Response:
    """Serve already encoded bytes through the existing authenticated map view."""
    zone = str(request.query.get("zone_artifact", ""))
    resource_id = str(request.query.get("artifact_id", ""))
    if not re.fullmatch(r"[1-9][0-9]{0,9}", zone) or not re.fullmatch(r"[a-f0-9]{64}", resource_id):
        raise web.HTTPBadRequest(text="Invalid zone artifact identity", headers={"Cache-Control": "no-store"})
    manager = getattr(coordinator, "map_artifacts", None)
    resource = manager.resource(int(zone), resource_id) if manager else None
    if resource is None:
        # Check cycle validity before ETag: after a reset even the old body's
        # matching If-None-Match must not produce 304 and resurrect a cached mask.
        raise web.HTTPGone(text="Artifact is not retained; refresh the manifest", headers={"Cache-Control": "no-store"})
    headers = {
        "ETag": f'"{resource_id}"',
        "Cache-Control": "private, no-cache, must-revalidate",
        "Vary": "Authorization",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    tags = str(request.headers.get("If-None-Match", "")).split(",")
    if any(tag.strip() == "*" or tag.strip().removeprefix("W/") == headers["ETag"] for tag in tags):
        return web.Response(status=304, headers=headers)
    return web.Response(body=resource["body"], content_type="image/svg+xml", headers=headers)


def _prepared_render_resource_response(
    coordinator: Any,
    request: web.Request,
    *,
    kind: str,
    query_key: str,
) -> web.Response:
    """Serve one already prepared JSON resource with conditional caching."""
    resource_id = str(request.query.get(query_key, ""))
    if not re.fullmatch(r"[a-f0-9]{64}", resource_id):
        raise web.HTTPBadRequest(
            text="Invalid prepared render resource identity",
            headers={"Cache-Control": "no-store"},
        )
    manager = getattr(coordinator, "prepared_render_model", None)
    resource = manager.resource(kind, resource_id) if manager else None
    if resource is None:
        raise web.HTTPGone(
            text="Prepared render resource is not retained; refresh the manifest",
            headers={"Cache-Control": "no-store"},
        )
    headers = {
        "ETag": f'"{resource_id}"',
        "Cache-Control": "private, no-cache, must-revalidate",
        "Vary": "Authorization",
        "X-Content-Type-Options": "nosniff",
    }
    tags = str(request.headers.get("If-None-Match", "")).split(",")
    if any(
        tag.strip() == "*"
        or tag.strip().removeprefix("W/") == headers["ETag"]
        for tag in tags
    ):
        if manager is not None:
            manager.record_resource_response(kind, not_modified=True)
        return web.Response(status=304, headers=headers)
    if manager is not None:
        manager.record_resource_response(kind, byte_length=len(resource["body"]))
    return web.Response(
        body=resource["body"],
        content_type="application/json",
        headers=headers,
    )


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
        if "static_render_model" in request.query:
            return _prepared_render_resource_response(
                coordinator,
                request,
                kind="static",
                query_key="static_render_model",
            )
        if "live_route_render" in request.query:
            return _prepared_render_resource_response(
                coordinator,
                request,
                kind="live",
                query_key="live_route_render",
            )
        if _query_requested(request, "render_model_manifest"):
            manager = getattr(coordinator, "prepared_render_model", None)
            if manager is None:
                raise web.HTTPServiceUnavailable(
                    headers={"Retry-After": "5", "Cache-Control": "no-store"}
                )
            response = self.json(manager.manifest())
            response.headers["Cache-Control"] = "no-store"
            return response
        if "zone_artifact" in request.query:
            return _zone_artifact_response(coordinator, request)
        if _query_requested(request, "artifacts_only"):
            manager = getattr(coordinator, "map_artifacts", None)
            if manager is None:
                raise web.HTTPServiceUnavailable(headers={"Retry-After": "30", "Cache-Control": "no-store"})
            response = self.json(manager.manifest())
            response.headers["Cache-Control"] = "no-store"
            return response
        if _query_requested(request, "current_cycle_only"):
            return self.json(await _async_current_cycle_only(coordinator))
        prepared_live_tail_only = _query_requested(
            request,
            "prepared_live_tail_only",
        )
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
                include_prepared_live_tail=(
                    prepared_live_tail_only
                    or _query_requested(request, "prepared_live_tail")
                ),
                prepared_live_tail_only=prepared_live_tail_only,
            )
        )

    _map_api._async_map_payload = _async_map_payload  # noqa: SLF001
    cls.get = get
    cls._phased_payload_installed = True
