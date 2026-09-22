"""Shared, prewarmed map render cache and authenticated per-zone SVG resources.

VendorTrailStore/ZoneLedger remain the authorities. This cache never resets a
cycle, changes a point, sends a mower command, or rewrites a History archive.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from copy import deepcopy
import hashlib
from html import escape
import json
import logging
import math
import time
from typing import Any
from urllib.parse import quote

from .vendor_trail_render_semantics import _cycle_identity, _mowing_width

_LOGGER = logging.getLogger(__name__)
RESOURCE_SCHEMA = 1
RETRY_SECONDS = 30


class MapArtifactUnavailable(RuntimeError):
    """No valid prepared render is available yet."""


def _resource(row: dict[str, Any], entry_id: str) -> dict[str, Any]:
    """Encode an immutable SVG in the executor, never in an HTTP handler."""
    artifact = row["artifact"]
    area = artifact.get("mowed_area") or {}
    bbox = area.get("bbox") or [0, 0, 0, 0]
    if len(bbox) != 4 or not all(math.isfinite(float(v)) for v in bbox):
        raise ValueError("Invalid artifact bounding box")
    pad = float(area.get("swath_width_m") or 0.25) / 2 + float(area.get("grid_size_m") or 0.025)
    x1, y1, x2, y2 = (float(v) for v in bbox)
    bounds = [x1 - pad, y1 - pad, x2 + pad, y2 + pad]
    viewbox = [bounds[0], bounds[1], max(0.001, bounds[2] - bounds[0]), max(0.001, bounds[3] - bounds[1])]
    text = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="'
        + " ".join(f"{v:.6f}" for v in viewbox)
        + '"><path fill="black" fill-rule="evenodd" d="'
        + escape(str(area.get("path_d") or ""), quote=True) + '"/></svg>'
    )
    body = text.encode("utf-8")
    build_key = tuple(row["artifact_revision"])
    resource_id = hashlib.sha256(
        json.dumps([RESOURCE_SCHEMA, entry_id, row["zone_id"], build_key], separators=(",", ":")).encode() + body
    ).hexdigest()
    return {
        "resource_id": resource_id,
        "zone_id": row["zone_id"], "cycle_id": row["cycle_id"],
        "build_key": build_key, "body": body,
        "descriptor": {
            "resource_id": resource_id, "format": "svg", "usage": "alpha_mask",
            "coordinate_space": "map_xy_m", "bounds": bounds,
            "geometry_revision": build_key[1], "byte_length": len(body),
            "url": f"/api/navimower/map/{quote(entry_id, safe='')}?zone_artifact={row['zone_id']}&artifact_id={resource_id}",
        },
    }


class MapArtifactManager:
    """One coalescing background builder shared by all cards for one mower."""

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator
        self._task: asyncio.Task | None = None
        self._desired: tuple | None = None
        self._finished_key: tuple | None = None
        self._cache: dict[str, Any] | None = None
        self._cache_scope: tuple | None = None
        self._published = asyncio.Event()
        self._resources: dict[int, list[dict[str, Any]]] = {}
        self._closed = False
        self._retry_at = 0.0
        self._checkpoint_task: asyncio.Task | None = None
        self._checkpoint_zones: set[int] = set()
        self._observed_activity: str | None = None
        self._observed_zone_id: int | None = None
        self.build_count = 0
        self.cache_hits = 0
        self.coalesced_updates = 0
        self.failure_count = 0
        self.last_build_ms: float | None = None
        self.last_error: str | None = None
        self.publication_revision = 0
        self.checkpoint_count = 0
        self.checkpoint_zone_build_count = 0
        self.checkpoint_coalesced_updates = 0
        self.last_checkpoint_ms: float | None = None
        self.last_checkpoint_reason: str | None = None

    @property
    def store(self):
        return self.coordinator.vendor_trail_store

    def _scope(self, snapshot=None):
        return (_cycle_identity(self.store), _mowing_width(snapshot if snapshot is not None else self.coordinator.data or {}))

    def _request(self, snapshot=None, map_zones=None):
        data = snapshot if snapshot is not None else self.coordinator.data or {}
        zones = map_zones if map_zones is not None else (data.get("map") or {}).get("zones", [])
        scope = self._scope(data)
        ids = {int(row["id"]) for row in zones if row.get("id") is not None}
        # Raw vendor geometry may advance every 10 seconds while the published
        # checkpoint remains intentionally frozen. Refresh this presentation
        # cache only when a published artifact (or a real fallback dependency)
        # changes, never for every retained-geometry observation.
        unowned = ids - self.store.owned_zone_ids()
        history_revision = getattr(self.coordinator.history, "trail_revision", None) if unowned else None
        artifact_state = tuple(
            sorted(
                (
                    int(zone_id),
                    str(row.get("cycle_id") or ""),
                    tuple(row.get("artifact_revision") or ()),
                )
                for zone_id, row in self.store.records.items()
                if row.get("vendor_owned")
            )
        )
        key = (
            scope,
            artifact_state,
            history_revision,
            (data.get("map") or {}).get("revision"),
            tuple(sorted(ids)),
        )
        return key, zones, scope

    def request_refresh(self, snapshot=None, map_zones=None) -> asyncio.Task | None:
        """Queue newest work from the coordinator event loop without awaiting it."""
        if self._closed:
            return None
        key, zones, scope = self._request(snapshot, map_zones)
        if not zones and not self.store.records:
            return None
        if self._desired is None or self._desired[0] != key:
            if self._task and not self._task.done():
                self.coalesced_updates += 1
            self._desired = (key, deepcopy(zones), scope)
        if key == self._finished_key or time.monotonic() < self._retry_at:
            return self._task
        if self._task is None or self._task.done():
            factory = getattr(self.coordinator.hass, "async_create_background_task", None)
            self._task = (
                factory(self._build(), "navimower-map-artifacts", eager_start=False)
                if factory else asyncio.create_task(self._build())
            )
        return self._task

    def request_checkpoint(
        self,
        *,
        zone_ids: set[int] | None = None,
        reason: str,
    ) -> asyncio.Task | None:
        """Queue an expensive vendor->SVG checkpoint only for lifecycle events."""
        if self._closed:
            return None
        selected = (
            set(self.store.owned_zone_ids())
            if zone_ids is None
            else {int(zone) for zone in zone_ids if int(zone) in self.store.records}
        )
        if not selected:
            return self._checkpoint_task
        if self._checkpoint_task and not self._checkpoint_task.done():
            self.checkpoint_coalesced_updates += 1
        self._checkpoint_zones.update(selected)
        self.last_checkpoint_reason = reason
        if self._checkpoint_task is None or self._checkpoint_task.done():
            factory = getattr(self.coordinator.hass, "async_create_background_task", None)
            self._checkpoint_task = (
                factory(self._checkpoint_worker(), "navimower-map-checkpoint", eager_start=False)
                if factory else asyncio.create_task(self._checkpoint_worker())
            )
        return self._checkpoint_task

    async def _checkpoint_worker(self) -> None:
        try:
            while self._checkpoint_zones and not self._closed:
                selected = set(self._checkpoint_zones)
                self._checkpoint_zones.difference_update(selected)
                before = {
                    zone: tuple((self.store.records.get(zone) or {}).get("artifact_revision") or ())
                    for zone in selected
                }
                started = time.perf_counter()
                width = _mowing_width(self.coordinator.data or {})
                await self.store.async_artifacts(
                    width,
                    build=True,
                    zone_ids=selected,
                )
                if self._closed:
                    return
                changed = sum(
                    tuple((self.store.records.get(zone) or {}).get("artifact_revision") or ())
                    != before.get(zone, ())
                    for zone in selected
                )
                if changed:
                    self.checkpoint_count += 1
                    self.checkpoint_zone_build_count += changed
                    self.last_checkpoint_ms = round(
                        (time.perf_counter() - started) * 1000,
                        2,
                    )
                    # Re-anchor already collected MQTT points to the newly
                    # published base before the next Map API read.
                    self.store.update_live_tail(
                        self.coordinator.data or {},
                        getattr(self.coordinator.history, "active_session", None),
                    )
                    self.store.schedule_save()
                    refresh = self.request_refresh()
                    if refresh is not None:
                        await refresh
                await asyncio.sleep(0)
        finally:
            self._checkpoint_task = None

    def observe(self, snapshot: dict[str, Any]) -> None:
        """Turn mower lifecycle transitions into sparse artifact checkpoints."""
        activity = str(snapshot.get("activity") or "").lower()
        raw_zone = snapshot.get("current_physical_zone_id")
        try:
            zone_id = int(raw_zone) if raw_zone is not None else None
        except (TypeError, ValueError):
            zone_id = None

        previous_activity = self._observed_activity
        previous_zone = self._observed_zone_id
        self._observed_activity = activity
        self._observed_zone_id = zone_id

        if previous_activity is None:
            return

        active = {"mowing", "paused"}
        settled = {"docked", "idle", "charging", "error"}

        if (
            previous_zone is not None
            and previous_zone != zone_id
            and previous_activity in active
        ):
            self.request_checkpoint(
                zone_ids={previous_zone},
                reason="zone_exit",
            )

        if activity in settled and previous_activity not in settled:
            self.request_checkpoint(reason="session_settled")

    def _notify(self):
        event = self._published
        self._published = asyncio.Event()
        event.set()

    def _valid_resources(self, zone_id: int) -> list[dict[str, Any]]:
        row = self.store.records.get(zone_id) or {}
        ledger_row = (self.store.ledger.get("zones") or {}).get(str(zone_id)) or {}
        cycle = ledger_row.get("cycle_key")
        width = _mowing_width(self.coordinator.data or {})
        if not row.get("vendor_owned") or row.get("cycle_id") != cycle:
            return []
        return [item for item in self._resources.get(zone_id, [])
                if item["cycle_id"] == cycle and item["build_key"][2] == width]

    def _prune(self):
        self._resources = {zone: valid for zone in self._resources
                           if (valid := self._valid_resources(zone))}

    async def _build(self):
        try:
            while not self._closed and self._desired:
                key, zones, scope = self._desired
                started = time.perf_counter()
                try:
                    # Existing beta13 builder still owns reset safety and source
                    # arbitration. No alternate trail reconstruction is introduced.
                    result = await self.coordinator.current_cycle_render_manager.async_get(zones)
                    if not isinstance(result, dict):
                        raise MapArtifactUnavailable("No current-cycle render")
                    rows = []
                    for zone_id, row in self.store.records.items():
                        revision = row.get("artifact_revision")
                        if not isinstance(row.get("artifact"), dict) or not isinstance(revision, (list, tuple)) or len(revision) != 4:
                            continue
                        if revision[0] != row.get("cycle_id") or revision[2] != scope[1]:
                            continue
                        cached = self._valid_resources(zone_id)
                        if cached and cached[0]["build_key"] == tuple(revision):
                            continue
                        # Shallow-copy only immutable completed artifact references;
                        # never copy raw vendor or MQTT point arrays into this cache.
                        rows.append({"zone_id": zone_id, "cycle_id": row["cycle_id"],
                                     "artifact": row["artifact"], "artifact_revision": list(revision)})
                    entry_id = str(self.coordinator.entry.entry_id)
                    resources = []
                    for row in rows:
                        resources.append(await self.coordinator.hass.async_add_executor_job(_resource, row, entry_id))
                    if self._closed:
                        return
                    if self._scope() != scope:
                        # A reset is not an error. Drop all in-flight old-cycle
                        # work; the coordinator/read path will queue the new scope.
                        self.request_refresh()
                        self._notify()
                        continue
                    self._cache = result
                    self._cache_scope = scope
                    self._finished_key = key
                    self._prune()
                    for item in resources:
                        zone = item["zone_id"]
                        previous = [old for old in self._resources.get(zone, []) if old["resource_id"] != item["resource_id"]]
                        self._resources[zone] = [item, *previous][:2]
                    self.publication_revision += 1
                    self.build_count += 1
                    self.last_build_ms = round((time.perf_counter() - started) * 1000, 2)
                    self.last_error = None
                    self._notify()
                    if self._desired[0] == key:
                        return
                except asyncio.CancelledError:
                    raise
                except Exception as err:  # noqa: BLE001 - optional render cache
                    self.failure_count += 1
                    self.last_error = type(err).__name__
                    self._retry_at = time.monotonic() + RETRY_SECONDS
                    _LOGGER.warning("Map artifact preparation failed; retaining same-cycle render", exc_info=True)
                    self._notify()
                    return
                await asyncio.sleep(0)
        finally:
            self._notify()

    async def async_get(self, map_zones):
        """Serve old-card contract from cache; cold callers share one build.

        Legacy cards consider a successful response current. Therefore they wait
        for one publication when geometry is pending instead of silently getting
        a stale/empty render that they would never request again. The new manifest
        and resource endpoints below NEVER wait for rendering.
        """
        key, _, scope = self._request(map_zones=map_zones)
        self.request_refresh(map_zones=map_zones)
        if self._cache_scope == scope and self._finished_key == key and self._cache is not None:
            self.cache_hits += 1
            return self._cache
        while self._task and not self._task.done() and not self._closed:
            event = self._published
            await event.wait()
            if self._cache_scope == self._scope() and self._cache is not None:
                return self._cache
        if self._cache_scope == self._scope() and self._cache is not None and not self._closed:
            return self._cache
        raise MapArtifactUnavailable("Current-cycle render is being prepared")

    def manifest(self) -> dict[str, Any]:
        """Return small ready-only descriptors, never SVG paths or point arrays."""
        self.request_refresh()
        self._prune()
        zones = []
        width = _mowing_width(self.coordinator.data or {})
        for zone_id in sorted(self.store.owned_zone_ids()):
            row = self.store.records[zone_id]
            resources = self._valid_resources(zone_id)
            resource = resources[0] if resources else None
            artifact_revision = row.get("artifact_revision") or [None] * 4
            artifact_geometry_revision = (
                artifact_revision[1] if len(artifact_revision) >= 2 else None
            )
            zones.append({
                "zone_id": zone_id, "cycle_id": row["cycle_id"], "vendor_owned": True,
                "pending": resource is None,
                "geometry_revision": row["geometry_revision"],
                "artifact_geometry_revision": artifact_geometry_revision,
                "geometry_ahead": bool(
                    resource is not None
                    and artifact_geometry_revision != row.get("geometry_revision")
                ),
                "artifact": deepcopy(resource["descriptor"]) if resource else None,
            })
        ids = {int(row["id"]) for row in (self.coordinator.data or {}).get("map", {}).get("zones", []) if row.get("id") is not None}
        return {
            "schema_version": RESOURCE_SCHEMA, "scope": "current_cycle_artifacts",
            "entry_id": str(self.coordinator.entry.entry_id),
            "coordinate_space": "map_xy_m", "cycle_identity": _cycle_identity(self.store),
            "publication_revision": self.publication_revision,
            "building": bool(self._task and not self._task.done()),
            "zones": zones, "fallback_zone_ids": sorted(ids - self.store.owned_zone_ids()),
        }

    def resource(self, zone_id: int, resource_id: str) -> dict[str, Any] | None:
        """Validate cycle ownership BEFORE allowing any body or HTTP 304."""
        if self._closed:
            return None
        return next((item for item in self._valid_resources(zone_id) if item["resource_id"] == resource_id), None)

    def diagnostics(self) -> dict[str, Any]:
        """Cached counters only: diagnostics never start work or expose paths."""
        ready = sum(bool(self._valid_resources(zone)) for zone in self.store.owned_zone_ids())
        dirty = 0
        for row in self.store.records.values():
            revision = row.get("artifact_revision") or []
            artifact_geometry = revision[1] if len(revision) >= 2 else None
            if artifact_geometry != row.get("geometry_revision"):
                dirty += 1
        return {
            "schema_version": RESOURCE_SCHEMA, "format": "svg",
            "mode": "event_checkpoint_plus_mqtt_live_tail",
            "build_count": self.build_count, "cache_hits": self.cache_hits,
            "coalesced_updates": self.coalesced_updates, "failure_count": self.failure_count,
            "last_build_ms": self.last_build_ms, "last_error": self.last_error,
            "publication_revision": self.publication_revision,
            "checkpoint_count": self.checkpoint_count,
            "checkpoint_zone_build_count": self.checkpoint_zone_build_count,
            "checkpoint_coalesced_updates": self.checkpoint_coalesced_updates,
            "last_checkpoint_ms": self.last_checkpoint_ms,
            "last_checkpoint_reason": self.last_checkpoint_reason,
            "dirty_zone_count": dirty,
            "checkpoint_building": bool(
                self._checkpoint_task and not self._checkpoint_task.done()
            ),
            "ready_zone_count": ready, "owned_zone_count": len(self.store.owned_zone_ids()),
            "resource_bytes": sum(len(item["body"]) for zone in self._resources for item in self._valid_resources(zone)),
            "building": bool(self._task and not self._task.done()),
        }

    async def async_shutdown(self):
        self._closed = True
        if self._checkpoint_task and not self._checkpoint_task.done():
            self._checkpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._checkpoint_task
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._checkpoint_zones.clear()
        self._cache = None
        self._resources.clear()
        self._notify()
