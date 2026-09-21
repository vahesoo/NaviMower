"""Cached backend map snapshots for notifications and automations."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
import logging
import time
from typing import Any

from .const import SWATH_WIDTH_M
from .map_snapshot_render import render_snapshot_png

_LOGGER = logging.getLogger(__name__)

ACTIVE_REFRESH_SECONDS = 60.0
_ACTIVE_ACTIVITIES = {"mowing", "paused", "returning"}


def _as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed


def _map_data(coordinator: Any) -> dict[str, Any]:
    data = coordinator.data or {}
    current = data.get("map")
    if isinstance(current, dict):
        return current
    try:
        fallback = coordinator._map_snapshot(  # noqa: SLF001
            coordinator._map_geometry or {},  # noqa: SLF001
            cutting_height_supported=data.get("cutting_height_supported"),
        )
    except Exception:  # noqa: BLE001
        return {}
    return fallback if isinstance(fallback, dict) else {}


def _vendor_segments(coordinator: Any) -> list[list[list[float]]]:
    store = getattr(coordinator, "vendor_trail_store", None)
    if store is None:
        return []
    result: list[list[list[float]]] = []
    for zone_id in sorted(store.records):
        row = store.records.get(zone_id) or {}
        if row.get("vendor_owned") is not True:
            continue
        clean: list[list[float]] = []
        for raw in row.get("points") or []:
            if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                continue
            try:
                x = float(raw[0])
                y = float(raw[1])
            except (TypeError, ValueError, OverflowError):
                continue
            clean.append([x, y])
        if len(clean) >= 2:
            result.append(clean)
    return result


def _position(data: dict[str, Any]) -> dict[str, float]:
    position = data.get("position")
    if isinstance(position, dict):
        x = _as_float(position.get("x"))
        y = _as_float(position.get("y"))
        heading = _as_float(position.get("heading"))
        if x is not None and y is not None:
            return {
                "x": x,
                "y": y,
                "heading": heading if heading is not None else 0.0,
            }
    x = _as_float(data.get("position_x"))
    y = _as_float(data.get("position_y"))
    heading = _as_float(data.get("heading"))
    if x is None or y is None:
        return {}
    return {"x": x, "y": y, "heading": heading if heading is not None else 0.0}


def _state_signature(data: dict[str, Any]) -> tuple[Any, ...]:
    map_data = data.get("map") or {}
    return (
        str(data.get("activity") or "").lower(),
        str(data.get("state_code") or ""),
        str(data.get("error_code") or ""),
        bool(data.get("docked")),
        str(
            data.get("current_physical_zone_id")
            or data.get("active_zone_progress_zone_id")
            or data.get("current_physical_zone")
            or ""
        ),
        str(map_data.get("revision") or "") if isinstance(map_data, dict) else "",
    )


async def _async_snapshot_source(coordinator: Any) -> dict[str, Any]:
    """Capture one self-contained render input from integration-owned state."""
    data = coordinator.data or {}
    map_data = _map_data(coordinator)
    zones = [
        dict(item)
        for item in map_data.get("zones") or []
        if isinstance(item, dict)
    ]

    current_cycle = {}
    manager = getattr(coordinator, "current_cycle_render_manager", None)
    if manager is not None:
        try:
            current_cycle = await manager.async_get(zones)
        except Exception:  # noqa: BLE001 - image remains useful without the swath
            _LOGGER.debug(
                "Current-cycle render was unavailable while building map snapshot",
                exc_info=True,
            )

    fallback_source = {}
    if current_cycle and manager is not None:
        candidate = getattr(manager, "_vendor_fallback_source", None)
        if isinstance(candidate, dict):
            fallback_source = deepcopy(candidate)

    try:
        map_payload = coordinator._map_payload_with_sessions([], None)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        map_payload = {}

    width = _as_float(data.get("mowing_path_width_m"))
    if width is None or not 0.1 <= width <= 2.0:
        width = SWATH_WIDTH_M

    live_segments: list[list[list[float]]] = []
    for segment in map_payload.get("trail_segments") or []:
        if not isinstance(segment, list):
            continue
        clean: list[list[float]] = []
        for raw in segment:
            if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                continue
            try:
                clean.append([float(raw[0]), float(raw[1])])
            except (TypeError, ValueError, OverflowError):
                continue
        if len(clean) >= 2:
            live_segments.append(clean)

    return {
        "map": deepcopy(map_data),
        "gate_areas": deepcopy(map_payload.get("gate_areas") or []),
        "vendor_segments": _vendor_segments(coordinator),
        "fallback_session": fallback_source,
        "live_route_segments": live_segments,
        "mowing_path_width_m": width,
        "position": _position(data),
        "activity": str(data.get("activity") or ""),
        "name": str(data.get("name") or "Navimow"),
        "show_zone_labels": True,
        "current_cycle_revision": (
            str(current_cycle.get("revision") or "")
            if isinstance(current_cycle, dict)
            else ""
        ),
    }


class MapSnapshotManager:
    """Maintain one latest PNG snapshot per mower with bounded refresh work."""

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._listeners: set[Callable[[], None]] = set()
        self._image: bytes | None = None
        self._last_rendered_at: datetime | None = None
        self._last_render_mono: float | None = None
        self._last_signature: tuple[Any, ...] | None = None
        self._closed = False
        self.render_reason: str | None = None
        self.render_duration_ms: float | None = None
        self.failure_count = 0
        self.last_error: str | None = None

    @property
    def image(self) -> bytes | None:
        return self._image

    @property
    def last_rendered_at(self) -> datetime | None:
        return self._last_rendered_at

    @property
    def render_age_seconds(self) -> float | None:
        if self._last_render_mono is None:
            return None
        return max(0.0, time.monotonic() - self._last_render_mono)

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)

        def remove() -> None:
            self._listeners.discard(listener)

        return remove

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Map snapshot listener failed", exc_info=True)

    async def async_refresh(
        self,
        *,
        reason: str,
        force: bool = False,
        require_fresh: bool = False,
    ) -> bytes:
        """Build a fresh snapshot and return only when the cache is updated."""
        async with self._lock:
            if self._closed:
                return self._image or b""
            if (
                not force
                and self._image is not None
                and self.render_age_seconds is not None
                and self.render_age_seconds < ACTIVE_REFRESH_SECONDS
            ):
                return self._image

            started = time.perf_counter()
            try:
                source = await _async_snapshot_source(self.coordinator)
                image = await self.hass.async_add_executor_job(
                    render_snapshot_png,
                    source,
                )
                if not image:
                    raise RuntimeError("snapshot renderer returned no image")
            except Exception as err:
                self.failure_count += 1
                self.last_error = type(err).__name__
                _LOGGER.warning(
                    "Navimower map snapshot render failed; keeping last good image",
                    exc_info=True,
                )
                if self._image is not None and not require_fresh:
                    return self._image
                raise

            self._image = image
            self._last_rendered_at = datetime.now(UTC)
            self._last_render_mono = time.monotonic()
            self.render_reason = str(reason)
            self.render_duration_ms = round(
                (time.perf_counter() - started) * 1000.0,
                2,
            )
            self.last_error = None
            self._notify()
            return image

    def request_refresh(self, *, reason: str, force: bool = False) -> None:
        """Queue at most one background refresh for noisy coordinator updates."""
        if self._closed:
            return
        if self._task is not None and not self._task.done():
            return

        async def runner() -> None:
            try:
                await self.async_refresh(reason=reason, force=force)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - async_refresh already logged it
                pass
            finally:
                self._task = None

        factory = getattr(self.hass, "async_create_background_task", None)
        self._task = (
            factory(
                runner(),
                f"Navimower map snapshot {self.coordinator.entry.entry_id}",
                eager_start=False,
            )
            if factory is not None
            else asyncio.create_task(runner())
        )

    def consider_auto_refresh(self, snapshot: dict[str, Any] | None = None) -> None:
        """Refresh immediately on major state changes, otherwise at most once/min."""
        data = snapshot or self.coordinator.data or {}
        signature = _state_signature(data)
        activity = str(data.get("activity") or "").strip().lower()

        if self._image is None:
            self._last_signature = signature
            self.request_refresh(reason="initial", force=True)
            return

        if self._last_signature is None:
            self._last_signature = signature
        elif signature != self._last_signature:
            self._last_signature = signature
            self.request_refresh(reason="state_change", force=True)
            return

        age = self.render_age_seconds
        if (
            activity in _ACTIVE_ACTIVITIES
            and (age is None or age >= ACTIVE_REFRESH_SECONDS)
        ):
            self.request_refresh(reason="active_interval", force=False)

    async def async_shutdown(self) -> None:
        self._closed = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._listeners.clear()


def get_map_snapshot_manager(coordinator: Any) -> MapSnapshotManager:
    manager = getattr(coordinator, "map_snapshot_manager", None)
    if not isinstance(manager, MapSnapshotManager):
        manager = MapSnapshotManager(coordinator)
        coordinator.map_snapshot_manager = manager
    return manager
