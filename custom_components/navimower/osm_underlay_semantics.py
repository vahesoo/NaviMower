"""Policy-compliant OpenStreetMap tile proxy for the Map Card.

The browser previously embedded tile.openstreetmap.org directly inside SVG. A
Home Assistant dashboard cannot control the browser User-Agent and some SVG
requests can also lose useful referrer information. Serve only the tiles the card
actually asks for through the authenticated map endpoint, identify Navimower to
the upstream service and keep a seven-day on-disk cache.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import time
from typing import Any

_OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
_OSM_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
_OSM_TIMEOUT_SECONDS = 20
_OSM_MAX_TILE_BYTES = 2 * 1024 * 1024
_OSM_USER_AGENT = (
    "Navimower-Home-Assistant "
    "(+https://github.com/vahesoo/NaviMower)"
)
_OSM_REFERER = "https://github.com/vahesoo/NaviMower"
_OSM_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_LOCKS: dict[tuple[str, int, int, int], asyncio.Lock] = {}
_INSTALLED = False


def _tile_coordinates(request: Any) -> tuple[int, int, int] | None:
    """Return validated z/x/y when this map request is an OSM tile request."""
    if str(request.query.get("osm_tile") or "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return None
    try:
        zoom = int(request.query["z"])
        tile_x = int(request.query["x"])
        tile_y = int(request.query["y"])
    except (KeyError, TypeError, ValueError):
        return (-1, -1, -1)
    if zoom < 0 or zoom > 19:
        return (-1, -1, -1)
    tile_count = 1 << zoom
    if not (0 <= tile_x < tile_count and 0 <= tile_y < tile_count):
        return (-1, -1, -1)
    return zoom, tile_x, tile_y


def _cache_path(hass: Any, zoom: int, tile_x: int, tile_y: int) -> Path:
    return Path(
        hass.config.path(
            ".storage",
            "navimower_osm_tiles",
            str(zoom),
            str(tile_x),
            f"{tile_y}.png",
        )
    )


def _cache_state(path: Path) -> tuple[bool, bool]:
    """Return (exists, fresh) without reading image bytes into memory."""
    try:
        stat = path.stat()
    except OSError:
        return False, False
    age = max(0.0, time.time() - stat.st_mtime)
    return stat.st_size > 0, age < _OSM_CACHE_TTL_SECONDS


def _write_tile(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".tmp-{time.time_ns()}")
    try:
        temporary.write_bytes(body)
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _file_response(path: Path):
    from aiohttp import web

    return web.FileResponse(
        path,
        headers={
            "Cache-Control": f"private, max-age={_OSM_CACHE_TTL_SECONDS}",
            "Content-Type": "image/png",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _osm_tile_response(request: Any, entry_id: str, coords: tuple[int, int, int]):
    from aiohttp import web
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    hass = request.app["hass"]
    # Reuse the normal map endpoint's config-entry authorization boundary. An
    # unknown entry must not turn this endpoint into a generic unauthenticated
    # tile relay.
    coordinators = hass.data.get("navimower") or {}
    if entry_id not in coordinators:
        raise web.HTTPNotFound(text="Unknown Navimower config entry")

    zoom, tile_x, tile_y = coords
    if zoom < 0:
        raise web.HTTPBadRequest(text="Invalid OpenStreetMap tile coordinates")

    path = _cache_path(hass, zoom, tile_x, tile_y)
    exists, fresh = await hass.async_add_executor_job(_cache_state, path)
    if exists and fresh:
        return _file_response(path)

    lock_key = (str(path), zoom, tile_x, tile_y)
    lock = _LOCKS.setdefault(lock_key, asyncio.Lock())
    async with lock:
        exists, fresh = await hass.async_add_executor_job(_cache_state, path)
        if exists and fresh:
            return _file_response(path)

        session = async_get_clientsession(hass)
        url = _OSM_TILE_URL.format(z=zoom, x=tile_x, y=tile_y)
        try:
            async with session.get(
                url,
                headers={
                    "User-Agent": _OSM_USER_AGENT,
                    "Referer": _OSM_REFERER,
                    "Accept": "image/png,image/*;q=0.8,*/*;q=0.5",
                },
                timeout=_OSM_TIMEOUT_SECONDS,
            ) as response:
                if response.status != 200:
                    await response.read()
                    if exists:
                        return _file_response(path)
                    raise web.HTTPBadGateway(
                        text=f"OpenStreetMap tile request failed ({response.status})"
                    )
                body = await response.read()
        except web.HTTPException:
            raise
        except Exception as err:  # noqa: BLE001 - stale cache remains usable.
            if exists:
                return _file_response(path)
            raise web.HTTPBadGateway(
                text="OpenStreetMap tile service could not be reached"
            ) from err

        if (
            not body.startswith(_OSM_PNG_MAGIC)
            or len(body) > _OSM_MAX_TILE_BYTES
        ):
            if exists:
                return _file_response(path)
            raise web.HTTPBadGateway(text="OpenStreetMap returned an invalid tile")

        await hass.async_add_executor_job(_write_tile, path, body)
        return _file_response(path)


def install_osm_underlay_semantics() -> None:
    """Add an authenticated OSM binary mode to the existing map endpoint."""
    global _INSTALLED
    if _INSTALLED:
        return

    from .map_api import NavimowerMapView

    if getattr(NavimowerMapView, "_navimower_osm_proxy_installed", False):
        _INSTALLED = True
        return

    previous_get = NavimowerMapView.get

    async def get_with_osm(self, request, entry_id: str):
        coords = _tile_coordinates(request)
        if coords is not None:
            return await _osm_tile_response(request, entry_id, coords)
        return await previous_get(self, request, entry_id)

    NavimowerMapView.get = get_with_osm
    NavimowerMapView._navimower_osm_proxy_installed = True
    _INSTALLED = True
