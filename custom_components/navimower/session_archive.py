"""Persistent prepared History render resources for completed Navimower sessions."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import logging
import time
from typing import Any
from urllib.parse import quote

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
_HISTORY_RESOURCE_SCHEMA_VERSION = 1


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


def _encode_resource(
    entry_id: str,
    session_id: str,
    render: dict[str, Any],
) -> dict[str, Any]:
    stable_render = deepcopy(render)
    # Build time is operational metadata, not render identity. Excluding it
    # keeps the content-addressed resource stable if an archive is regenerated.
    stable_render.pop("generated_at", None)
    payload = {
        "schema_version": _HISTORY_RESOURCE_SCHEMA_VERSION,
        "scope": "prepared_history_render",
        "coordinate_space": "map_xy_m",
        "session_id": str(session_id),
        "render_schema_version": stable_render.get("version"),
        "source": deepcopy(stable_render.get("source") or {}),
        "render": stable_render,
    }
    body = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    resource_id = hashlib.sha256(
        b"navimower-prepared-history:"
        + str(_HISTORY_RESOURCE_SCHEMA_VERSION).encode()
        + b":"
        + entry_id.encode()
        + b":"
        + str(session_id).encode()
        + b":"
        + body
    ).hexdigest()
    return {
        "resource_id": resource_id,
        "session_id": str(session_id),
        "body": body,
        "payload": payload,
        "descriptor": {
            "resource_id": resource_id,
            "format": "json",
            "scope": "prepared_history_render",
            "coordinate_space": "map_xy_m",
            "session_id": str(session_id),
            "render_schema_version": render.get("version"),
            "byte_length": len(body),
            "url": (
                f"/api/navimower/history-resource/{quote(entry_id, safe='')}/"
                f"{resource_id}"
            ),
        },
    }


class SessionArchiveManager:
    """Prepare immutable, content-addressed render resources for History."""

    def __init__(self, hass: HomeAssistant, entry_id: str, coordinator) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.coordinator = coordinator
        self.history = coordinator.history
        self._unsub = None
        self._scan_task: asyncio.Task | None = None
        self._prewarm_task: asyncio.Task | None = None
        self._pending = False
        self._stopped = False
        self._last_revision: int | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._resources_by_session: dict[str, dict[str, Any]] = {}
        self._resources_by_id: dict[str, dict[str, Any]] = {}

        # Existing counters remain stable for diagnostics/backward compatibility.
        self.cache_hits = 0
        self.build_count = 0
        self.failure_count = 0
        self.scan_failure_count = 0
        self.last_build_ms: float | None = None
        self.last_error: str | None = None
        self.last_session_id: str | None = None

        # Prepared History lifecycle and transport counters.
        self.prewarm_started = False
        self.prewarm_complete = False
        self.prewarm_build_count = 0
        self.prewarm_cache_hit_count = 0
        self.lazy_build_count = 0
        self.legacy_render_reads = 0
        self.publication_revision = 0
        self.manifest_reads = 0
        self.resource_reads = 0
        self.resource_bytes_served_total = 0
        self.resource_not_modified_count = 0
        self._manifest_first_read_mono: float | None = None
        self._manifest_last_read_mono: float | None = None
        self._resource_first_read_mono: float | None = None
        self._resource_last_read_mono: float | None = None

    def start(self) -> None:
        """Watch History lifecycle and prewarm every retained completed session."""
        if self._unsub is not None:
            return
        self._last_revision = self.history.active_session_no
        self._unsub = self.coordinator.async_add_listener(self._state_updated)
        self.prewarm_started = True
        self._prewarm_task = self.hass.async_create_task(
            self._async_prewarm_retained(),
            f"Prewarm Navimower History renders {self.entry_id}",
        )

    async def async_stop(self) -> None:
        self._stopped = True
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        tasks = [self._scan_task, self._prewarm_task]
        self._scan_task = None
        self._prewarm_task = None
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
        for task in tasks:
            if task is not None and not task.done():
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
        if self._scan_task is not None and not self._scan_task.done():
            self._pending = True
            return
        self._scan_task = self.hass.async_create_task(
            self._async_scan_latest(),
            f"Prepare Navimower session render {self.entry_id}",
        )

    def _eligible_rows(self) -> list[dict[str, Any]]:
        try:
            payload = self.history.sessions_index_payload()
        except Exception:
            return []
        return [
            dict(row)
            for row in (payload.get("sessions") if isinstance(payload, dict) else []) or []
            if isinstance(row, dict)
            and row.get("id")
            and not row.get("active")
            and int(row.get("point_count") or 0) >= 2
        ]

    def _prune_memory(self) -> None:
        retained = {str(row.get("id")) for row in self._eligible_rows()}
        for session_id in list(self._resources_by_session):
            if session_id in retained:
                continue
            resource = self._resources_by_session.pop(session_id)
            self._resources_by_id.pop(str(resource.get("resource_id") or ""), None)

    async def _async_prewarm_retained(self) -> None:
        try:
            # Preserve the existing completion-settle guard before treating
            # completed sessions as immutable prepared History resources.
            await asyncio.sleep(_ARCHIVE_SETTLE_SECONDS)
            for row in self._eligible_rows():
                if self._stopped:
                    return
                session_id = str(row["id"])
                try:
                    await self._async_get(session_id, reason="prewarm")
                except Exception:  # noqa: BLE001
                    self.scan_failure_count += 1
                    _LOGGER.warning(
                        "Could not prewarm Navimower History render %s",
                        session_id,
                        exc_info=True,
                    )
                await asyncio.sleep(0)
            self._prune_memory()
            self.prewarm_complete = True
        except asyncio.CancelledError:
            raise
        finally:
            self._prewarm_task = None

    async def _async_scan_latest(self) -> None:
        try:
            await asyncio.sleep(_ARCHIVE_SETTLE_SECONDS)
            latest = next(iter(self._eligible_rows()), None)
            if latest and latest.get("id"):
                await self._async_get(str(latest["id"]), reason="prewarm")
            self._prune_memory()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            self.scan_failure_count += 1
            self.last_error = type(err).__name__
            _LOGGER.warning(
                "Could not prepare the latest Navimower session render",
                exc_info=True,
            )
        finally:
            self._scan_task = None
            if self._pending and not self._stopped:
                self._pending = False
                self._schedule_scan()

    def _publish_resource(
        self,
        session_id: str,
        render: dict[str, Any],
    ) -> dict[str, Any]:
        resource = _encode_resource(self.entry_id, session_id, render)
        previous = self._resources_by_session.get(str(session_id))
        if previous and previous.get("resource_id") != resource["resource_id"]:
            self._resources_by_id.pop(str(previous.get("resource_id") or ""), None)
        if not previous or previous.get("resource_id") != resource["resource_id"]:
            self.publication_revision += 1
        self._resources_by_session[str(session_id)] = resource
        self._resources_by_id[resource["resource_id"]] = resource
        return resource

    async def _async_get(
        self,
        session_id: str,
        *,
        reason: str,
    ) -> dict[str, Any] | None:
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
                self.cache_hits += 1
                if reason == "prewarm":
                    self.prewarm_cache_hit_count += 1
                self._publish_resource(requested, cached)
                self.last_session_id = requested
                self.last_error = None
                return deepcopy(cached)

            render_session = deepcopy(session)
            if render_session.get("mowing_path_width_m") is None:
                width = (self.coordinator.data or {}).get("mowing_path_width_m")
                if width is not None:
                    render_session["mowing_path_width_m"] = width
            started = time.perf_counter()
            try:
                artifact = await self.hass.async_add_executor_job(
                    build_session_svg_archive,
                    render_session,
                )
            except Exception as err:
                self.failure_count += 1
                self.last_error = type(err).__name__
                raise
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

            try:
                await store.async_save(artifact)
            except Exception as err:
                self.failure_count += 1
                self.last_error = type(err).__name__
                raise

            self.build_count += 1
            if reason == "prewarm":
                self.prewarm_build_count += 1
            else:
                self.lazy_build_count += 1
            self.last_build_ms = round((time.perf_counter() - started) * 1000.0, 2)
            self._publish_resource(requested, artifact)
            self.last_session_id = requested
            self.last_error = None
            return deepcopy(artifact)

    async def async_get(self, session_id: str) -> dict[str, Any] | None:
        """Backward-compatible on-demand session-render endpoint source."""
        self.legacy_render_reads += 1
        return await self._async_get(str(session_id), reason="legacy")

    def discovery(self) -> dict[str, Any]:
        entry = quote(self.entry_id, safe="")
        return {
            "schema_version": _HISTORY_RESOURCE_SCHEMA_VERSION,
            "scope": "prepared_history",
            "coordinate_space": "map_xy_m",
            "ready_only": True,
            "manifest_url": f"/api/navimower/history-manifest/{entry}",
            "resource_url_template": (
                f"/api/navimower/history-resource/{entry}/{{resource_id}}"
            ),
            "legacy_session_render_url_template": (
                f"/api/navimower/session-render/{entry}/{{session_id}}"
            ),
            "capabilities": {
                "content_addressed_resources": True,
                "etag": True,
                "immutable_completed_sessions": True,
                "retained_session_prewarm": True,
                "legacy_session_render_fallback": True,
            },
        }

    def manifest(self) -> dict[str, Any]:
        """Return only retained metadata plus descriptors for ready resources."""
        self.manifest_reads += 1
        now = time.monotonic()
        if self._manifest_first_read_mono is None:
            self._manifest_first_read_mono = now
        self._manifest_last_read_mono = now
        self._prune_memory()

        index = self.history.sessions_index_payload()
        rows = [
            dict(row)
            for row in (index.get("sessions") if isinstance(index, dict) else []) or []
            if isinstance(row, dict) and row.get("id")
        ]
        sessions: list[dict[str, Any]] = []
        eligible = 0
        ready = 0
        for row in rows:
            session_id = str(row["id"])
            is_eligible = not row.get("active") and int(row.get("point_count") or 0) >= 2
            resource = self._resources_by_session.get(session_id) if is_eligible else None
            if is_eligible:
                eligible += 1
            if resource is not None:
                ready += 1
            sessions.append(
                {
                    **row,
                    "render_ready": resource is not None,
                    "render": deepcopy(resource["descriptor"]) if resource else None,
                }
            )

        return {
            "schema_version": _HISTORY_RESOURCE_SCHEMA_VERSION,
            "scope": "prepared_history",
            "entry_id": self.entry_id,
            "coordinate_space": "map_xy_m",
            "ready_only": True,
            "retention_days": index.get("retention_days"),
            "active_session_id": index.get("active_session_id"),
            "retained_session_count": len(rows),
            "eligible_session_count": eligible,
            "ready_session_count": ready,
            "pending_session_count": max(0, eligible - ready),
            "prewarm_started": self.prewarm_started,
            "prewarm_complete": self.prewarm_complete,
            "publication_revision": self.publication_revision,
            "resource_url_template": self.discovery()["resource_url_template"],
            "legacy_session_render_url_template": self.discovery()[
                "legacy_session_render_url_template"
            ],
            "sessions": sessions,
        }

    def resource(self, resource_id: str) -> dict[str, Any] | None:
        resource = self._resources_by_id.get(str(resource_id))
        if resource is not None:
            self.resource_reads += 1
            now = time.monotonic()
            if self._resource_first_read_mono is None:
                self._resource_first_read_mono = now
            self._resource_last_read_mono = now
        return resource

    def record_resource_response(
        self,
        *,
        byte_length: int = 0,
        not_modified: bool = False,
    ) -> None:
        self.resource_bytes_served_total += max(0, int(byte_length))
        if not_modified:
            self.resource_not_modified_count += 1

    @staticmethod
    def _age_seconds(value: float | None) -> float | None:
        if value is None:
            return None
        return round(max(0.0, time.monotonic() - value), 1)

    def diagnostics(self) -> dict[str, Any]:
        """Return cached-only Prepared History health without loading Stores."""
        self._prune_memory()
        try:
            retained_rows = [
                row
                for row in self.history.sessions_index_payload().get("sessions") or []
                if isinstance(row, dict) and row.get("id")
            ]
        except Exception:
            retained_rows = []
        eligible_ids = {
            str(row.get("id"))
            for row in retained_rows
            if not row.get("active") and int(row.get("point_count") or 0) >= 2
        }
        ready_ids = eligible_ids.intersection(self._resources_by_session)
        return {
            "store_version": _ARCHIVE_STORE_VERSION,
            "prepared_history_schema_version": _HISTORY_RESOURCE_SCHEMA_VERSION,
            "started": self._unsub is not None and not self._stopped,
            "building": bool(
                (self._scan_task is not None and not self._scan_task.done())
                or (self._prewarm_task is not None and not self._prewarm_task.done())
            ),
            "pending": self._pending,
            "prewarm_started": self.prewarm_started,
            "prewarm_complete": self.prewarm_complete,
            "retained_session_count": len(retained_rows),
            "eligible_session_count": len(eligible_ids),
            "ready_session_count": len(ready_ids),
            "pending_session_count": max(0, len(eligible_ids) - len(ready_ids)),
            "cache_hits": self.cache_hits,
            "build_count": self.build_count,
            "prewarm_build_count": self.prewarm_build_count,
            "prewarm_cache_hit_count": self.prewarm_cache_hit_count,
            "lazy_build_count": self.lazy_build_count,
            "legacy_render_reads": self.legacy_render_reads,
            "publication_revision": self.publication_revision,
            "manifest_reads": self.manifest_reads,
            "manifest_first_read_age_s": self._age_seconds(
                self._manifest_first_read_mono
            ),
            "manifest_last_read_age_s": self._age_seconds(
                self._manifest_last_read_mono
            ),
            "resource_reads": self.resource_reads,
            "resource_first_read_age_s": self._age_seconds(
                self._resource_first_read_mono
            ),
            "resource_last_read_age_s": self._age_seconds(
                self._resource_last_read_mono
            ),
            "resource_bytes_ready_total": sum(
                len(resource["body"])
                for session_id, resource in self._resources_by_session.items()
                if session_id in ready_ids
            ),
            "resource_bytes_served_total": self.resource_bytes_served_total,
            "resource_not_modified_count": self.resource_not_modified_count,
            "failure_count": self.failure_count,
            "scan_failure_count": self.scan_failure_count,
            "last_build_ms": self.last_build_ms,
            "last_error": self.last_error,
            "last_session_id": self.last_session_id,
        }

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
