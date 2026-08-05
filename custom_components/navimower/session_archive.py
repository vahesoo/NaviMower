"""Persistent SVG render cache for completed Navimower sessions."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .session_svg import (
    build_session_svg_archive,
    render_matches_session,
)

_LOGGER = logging.getLogger(__name__)
_ARCHIVE_STORE_VERSION = 1
_ARCHIVE_SETTLE_SECONDS = 2


def _safe_session_id(session_id: str) -> str:
    return "".join(ch for ch in str(session_id) if ch.isalnum() or ch in "_-")


def _archive_store(hass: HomeAssistant, entry_id: str, session_id: str) -> Store:
    key = f"{DOMAIN}_session_render_{entry_id}_{_safe_session_id(session_id)}"
    try:
        return Store(
            hass,
            _ARCHIVE_STORE_VERSION,
            key,
            serialize_in_event_loop=False,
        )
    except TypeError:
        return Store(hass, _ARCHIVE_STORE_VERSION, key)


def _history_index_store(hass: HomeAssistant, entry_id: str) -> Store:
    key = f"{DOMAIN}_sessions_{entry_id}"
    try:
        return Store(hass, 1, key, serialize_in_event_loop=False)
    except TypeError:
        return Store(hass, 1, key)


class SessionArchiveManager:
    """Generate and persist one compact render artifact per completed session."""

    def __init__(self, hass: HomeAssistant, entry_id: str, coordinator) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.coordinator = coordinator
        self.history = coordinator.history
        self._unsub = None
        self._task: asyncio.Task | None = None
        self._pending = False
        self._stopped = False
        self._last_revision: int | None = None
        self._locks: dict[str, asyncio.Lock] = {}

    def start(self) -> None:
        """Watch session start/finish revisions and archive the latest completion."""
        if self._unsub is not None:
            return
        self._last_revision = self.history.active_session_no
        self._unsub = self.coordinator.async_add_listener(self._state_updated)
        self._schedule_scan()

    async def async_stop(self) -> None:
        self._stopped = True
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _state_updated(self) -> None:
        revision = self.history.active_session_no
        if revision == self._last_revision:
            return
        self._last_revision = revision
        self._schedule_scan()

    def _schedule_scan(self) -> None:
        if self._stopped:
            return
        if self._task is not None and not self._task.done():
            self._pending = True
            return
        self._task = self.hass.async_create_task(
            self._async_scan_latest(),
            f"Prepare Navimower session render {self.entry_id}",
        )

    async def _async_scan_latest(self) -> None:
        try:
            await asyncio.sleep(_ARCHIVE_SETTLE_SECONDS)
            summaries = self.history.session_summaries(include_points=False)
            latest = next(
                (
                    row
                    for row in reversed(summaries)
                    if not row.get("active") and int(row.get("point_count") or 0) >= 2
                ),
                None,
            )
            if latest and latest.get("id"):
                await self.async_get(str(latest["id"]))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Could not prepare the latest Navimower session render",
                exc_info=True,
            )
        finally:
            self._task = None
            if self._pending and not self._stopped:
                self._pending = False
                self._schedule_scan()

    async def async_get(self, session_id: str) -> dict[str, Any] | None:
        """Return a current archive, building it lazily when needed."""
        requested = str(session_id)
        lock = self._locks.setdefault(requested, asyncio.Lock())
        async with lock:
            session = await self.history.async_session_payload(requested)
            if not isinstance(session, dict) or session.get("active"):
                return None

            store = _archive_store(self.hass, self.entry_id, requested)
            try:
                cached = await store.async_load()
            except Exception:  # noqa: BLE001
                cached = None
            if render_matches_session(cached, session):
                return deepcopy(cached)

            artifact = await self.hass.async_add_executor_job(
                build_session_svg_archive,
                deepcopy(session),
            )
            if artifact is None:
                return None

            # Re-read after CPU work. A docked session can reopen during the
            # five-minute continuation window; never publish that stale archive.
            latest = await self.history.async_session_payload(requested)
            if (
                not isinstance(latest, dict)
                or latest.get("active")
                or not render_matches_session(artifact, latest)
            ):
                return None

            await store.async_save(artifact)
            return deepcopy(artifact)

    @classmethod
    async def async_remove_all(cls, hass: HomeAssistant, entry_id: str) -> None:
        """Remove all derived render Stores when a config entry is deleted."""
        index = _history_index_store(hass, entry_id)
        try:
            data = await index.async_load()
        except Exception:  # noqa: BLE001
            data = None
        session_ids = [
            str(item.get("id"))
            for item in (data.get("sessions") if isinstance(data, dict) else []) or []
            if isinstance(item, dict) and item.get("id")
        ]
        for session_id in session_ids:
            try:
                await _archive_store(hass, entry_id, session_id).async_remove()
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Could not remove Navimower session render %s",
                    session_id,
                    exc_info=True,
                )
