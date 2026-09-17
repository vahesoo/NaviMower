"""Narrow semantic corrections layered on the main Navimower coordinator."""
from __future__ import annotations

import logging
import time
from typing import Any

from .api import NavimowAuthError, NavimowError
from .coordinator import (
    NavimowCoordinator as _BaseNavimowCoordinator,
    _parse_map_detail,
    _parse_map_detail_plain,
    _points_xy,
    state_store,
)
from .georeference import (
    georeference_from_compressed_map_detail,
    georeference_from_plain_map_detail,
    update_georeference,
)
from .map_identifiers import resolve_map_identifiers
from .vendor_trail_store import VendorTrailStore
from .zone_ledger import mark_explicit_reset
from .vendor_trail import (
    VENDOR_TRAIL_ACTIVE_TTL_SECONDS,
    VendorTrailCurrentCycleRenderManager,
    active_vendor_row,
    coverage_by_zone,
    current_vendor_rows,
    decode_vendor_trail_response,
    normalize_vendor_trail_row,
    trim_mqtt_tail_segments,
    utc_now_iso,
)

_LOGGER = logging.getLogger(__name__)


def _valid_map_version(value: Any) -> bool:
    """Return whether index2 exposes a usable vendor map revision."""
    return value is not None and str(value).strip() not in {"", "0"}


class NavimowCoordinator(_BaseNavimowCoordinator):
    """Coordinator with strict mowing timestamps and revision-aware map refresh."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.vendor_trail_store = VendorTrailStore(self.hass, self.entry.entry_id)
        # Poll diagnostics are ephemeral; retained geometry belongs to the store.
        self._vendor_trail_cache: dict[int, dict[str, Any]] = {}
        self._vendor_trail_revision = 0
        self._vendor_trail_last_attempt_mono: float | None = None
        self._vendor_trail_last_success_mono: float | None = None
        self._vendor_trail_last_fetch_utc: str | None = None
        self._vendor_trail_last_error: str | None = None
        self._vendor_trail_last_zone_ids: tuple[int, ...] = ()
        self.current_cycle_render_manager = VendorTrailCurrentCycleRenderManager(self)

    async def async_load_persistent_state(self) -> None:
        """Restore state and force one map refresh for pre-georeference caches."""
        await self.vendor_trail_store.async_load()
        self._zone_ledger_shadow_state = self.vendor_trail_store.ledger
        self._vendor_trail_cache = self.vendor_trail_store.records
        await super().async_load_persistent_state()
        if (
            self._map_geometry is not None
            and not self._map_geometry.get("georeference")
            and not self._map_geometry.get("_georeference_calibration")
        ):
            # v0.4.3 caches contain perfectly usable local geometry but not the
            # WGS84 tie point/calibration state needed by multi-mower/site views.
            # Keep displaying the cached map immediately, then re-decode it once.
            self._map_cache_key = None

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        await self.vendor_trail_store.async_flush()

    def start_new_mowing_cycle(self, zone_ids=None, *, source: str) -> bool:
        # Called only after the existing successful command path. The ledger
        # resets precisely the explicitly selected zones, never task membership.
        selected = zone_ids or [row.get("id") for row in (self.data or {}).get("map", {}).get("zones", [])]
        state, _events = mark_explicit_reset(
            self.vendor_trail_store.ledger, selected,
            observed_at_ms=int(time.time() * 1000), reason=source,
        )
        self._zone_ledger_shadow_state = state
        self.vendor_trail_store.reconcile(state)
        self.vendor_trail_store.schedule_save()
        return super().start_new_mowing_cycle(zone_ids, source=source)

    def _accept_vendor_observations(self, snapshot: dict[str, Any]) -> None:
        store = self.vendor_trail_store
        store.reconcile(self._zone_ledger_shadow_state)
        for row in snapshot.pop("_vendor_trail_observations", []):
            store.accept(row)
        store.update_live_tail(snapshot, self.history.active_session)
        self._vendor_trail_cache = store.records
        self._vendor_trail_revision = store.revision
        snapshot["vendor_trail_revision"] = f"{store.revision}:{store.ledger.get('revision', 0)}:{self.history.active_session_no}"
        store.schedule_save()

    def _build_zone_details(
        self,
        coverage: dict[str, Any] | None,
        global_height: int | None,
        cutting_height_supported: bool,
    ) -> list[dict[str, Any]]:
        """Keep vendor coverage timestamps out of user-facing mowing history."""
        details = super()._build_zone_details(
            coverage,
            global_height,
            cutting_height_supported,
        )
        for detail in details:
            detail.pop("last_started_at", None)
            detail.pop("last_mowed_at", None)
        return details

    def _fetch_endpoint(
        self,
        raw: dict[str, Any],
        key: str,
        getter: Any,
        *,
        ttl: int,
        now: float,
    ) -> bool:
        """Make an index2 mapVersion change invalidate geometry in the same poll.

        Location/map-list have much longer idle TTLs than index2. During map editing
        index2.mapVersion can therefore advance while the cached geometry still
        describes the previous revision. Force location + map-list due and clear
        the geometry key when a real version change is observed so the coordinator
        downloads the new map detail immediately in this poll cycle.
        """
        previous_version = None
        if key == "index2":
            previous = raw.get("index2")
            if isinstance(previous, dict):
                previous_version = previous.get("mapVersion")

        success = super()._fetch_endpoint(
            raw,
            key,
            getter,
            ttl=ttl,
            now=now,
        )
        if key != "index2" or not success:
            return success

        current = raw.get("index2")
        current_version = current.get("mapVersion") if isinstance(current, dict) else None
        if (
            _valid_map_version(previous_version)
            and _valid_map_version(current_version)
            and str(current_version) != str(previous_version)
        ):
            self._map_cache_key = None
            for dependent in ("location", "map_list"):
                status = self._endpoint_status.get(dependent)
                if isinstance(status, dict):
                    status["last_attempt_mono"] = None
        return success

    def _refresh_vendor_trail_debug(self, snapshot: dict[str, Any]) -> None:
        """Poll retained vendor geometry at a bounded active-mowing cadence."""
        active = self._private_poll_active()
        zone_ids = sorted({int(row["id"]) for row in (snapshot.get("map") or {}).get("zones", []) if row.get("id")} | set(coverage_by_zone(snapshot)))
        if not zone_ids:
            return
        zone_key = tuple(zone_ids)
        now = time.monotonic()
        due = (
            self._vendor_trail_last_attempt_mono is None
            or zone_key != self._vendor_trail_last_zone_ids
            or now - self._vendor_trail_last_attempt_mono
            >= (VENDOR_TRAIL_ACTIVE_TTL_SECONDS if active else 300)
        )
        if not due:
            return

        self._vendor_trail_last_attempt_mono = now
        self._vendor_trail_last_zone_ids = zone_key
        try:
            payload = self.client.call(
                "/vehicle/trail/get-path-info-data-compress",
                {"vehicle_sn": self.sn, "partitionList": list(zone_ids)},
            )
            decoded = decode_vendor_trail_response(payload)
            fresh_coverage = coverage_by_zone(snapshot)
            decoded_by_id: dict[int, dict[str, Any]] = {}
            for row in decoded:
                if not isinstance(row, dict):
                    continue
                try:
                    zone_id = int(float(row.get("partitionId")))
                except (TypeError, ValueError):
                    continue
                if zone_id > 0:
                    decoded_by_id[zone_id] = row
            observations = []
            for zone_id in zone_ids:
                raw_row = decoded_by_id.get(zone_id)
                if raw_row is None:
                    continue
                normalized = normalize_vendor_trail_row(
                    raw_row,
                    coverage=fresh_coverage.get(zone_id),
                )
                if normalized is None:
                    continue
                observations.append(normalized)
            # Consumed on the event loop only after ZoneLedger has reduced this
            # exact poll's coverage. Never mutate the store in the worker thread.
            snapshot["_vendor_trail_observations"] = observations
            self._vendor_trail_last_success_mono = now
            self._vendor_trail_last_fetch_utc = utc_now_iso()
            self._vendor_trail_last_error = None
        except NavimowAuthError:
            raise
        except Exception as err:  # noqa: BLE001 - beta telemetry must not break state.
            self._vendor_trail_last_error = str(err)
            _LOGGER.debug(
                "Vendor trail debug refresh failed; retaining previous geometry: %s",
                err,
            )

    def _fetch_blocking(self) -> dict[str, Any]:
        """Run the normal private poll, then the bounded retained-trail probe."""
        snapshot = super()._fetch_blocking()
        self._refresh_vendor_trail_debug(snapshot)
        return snapshot

    def _maybe_fetch_map(self, raw: dict[str, Any]) -> None:
        """Decode local geometry and any optional vendor WGS84 hint in one fetch."""
        location = raw.get("location") or {}
        map_id, map_base_id, edit_time = resolve_map_identifiers(
            location, raw.get("map_list")
        )
        if map_id is None or map_base_id is None:
            return

        key = (str(map_id), str(map_base_id), str(edit_time))
        if self._map_geometry is not None and self._map_cache_key == key:
            return

        geometry: dict[str, Any] | None = None
        vendor_georeference: dict[str, Any] | None = None
        try:
            plain = self.client.map_detail_plain(
                self.sn, str(map_id), str(map_base_id)
            )
            geometry = _parse_map_detail_plain(plain)
            vendor_georeference = georeference_from_plain_map_detail(plain)
        except NavimowAuthError:
            raise
        except NavimowError:
            geometry = None

        if geometry is None:
            try:
                blob = self.client.map_detail(
                    self.sn, str(map_id), str(map_base_id)
                )
                geometry = _parse_map_detail(blob)
                vendor_georeference = georeference_from_compressed_map_detail(blob)
            except NavimowAuthError:
                raise
            except NavimowError:
                return
        if geometry is None:
            return

        geometry["map_id"] = str(map_id)
        geometry["map_base_id"] = str(map_base_id)
        geometry["edit_time"] = str(edit_time or "")
        geometry["revision"] = "|".join(key)

        index2 = raw.get("index2")
        map_version = index2.get("mapVersion") if isinstance(index2, dict) else None
        if _valid_map_version(map_version):
            geometry["map_version"] = str(map_version)
            geometry["revision"] = f"{geometry['revision']}|v:{map_version}"

        # Explicit vendor tie points are a useful bootstrap/check where present,
        # but the universal path learns from ordinary cloud XY/GPS location pairs.
        if vendor_georeference is not None:
            geometry["_vendor_georeference"] = vendor_georeference
            geometry["georeference"] = vendor_georeference

        # Preserve the existing optional station-map behavior. This geometry is
        # in a docking-local frame and is intentionally not georeferenced.
        try:
            station_raw = self.client.station_map(
                self.sn, str(map_id), str(map_base_id)
            )
            pts = _points_xy((station_raw or {}).get("points"))
            if pts:
                geometry["station_map"] = {
                    "points": pts,
                    "start_from_pile": bool(
                        (station_raw or {}).get("start_from_pile")
                    ),
                }
        except NavimowAuthError:
            raise
        except NavimowError:
            pass

        self._map_geometry = geometry
        self._map_cache_key = key
        self._map_dirty = True

    @staticmethod
    def _map_snapshot(
        map_geometry: dict[str, Any],
        *,
        cutting_height_supported: bool | None = None,
    ) -> dict[str, Any]:
        """Expose map revision and normalized georeference to the Map API."""
        snapshot = _BaseNavimowCoordinator._map_snapshot(
            map_geometry,
            cutting_height_supported=cutting_height_supported,
        )
        snapshot["map_version"] = map_geometry.get("map_version")
        georeference = map_geometry.get("georeference")
        snapshot["georeference"] = (
            dict(georeference) if isinstance(georeference, dict) else None
        )
        return snapshot

    def _parse(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Learn/validate one stable local-map -> WGS84 transform per revision."""
        snapshot = super()._parse(raw)
        georeference = update_georeference(
            self._map_geometry,
            raw.get("location"),
        )
        snapshot["georeference"] = georeference
        map_data = snapshot.get("map")
        if isinstance(map_data, dict):
            map_data = dict(map_data)
            map_data["georeference"] = georeference
            snapshot["map"] = map_data
        return snapshot

    def _vendor_trail_debug_payload(
        self,
        snapshot: dict[str, Any],
        tail_metrics: dict[str, Any],
    ) -> dict[str, Any]:
        now = time.monotonic()
        active_row = active_vendor_row(snapshot, self._vendor_trail_cache)
        current_rows = current_vendor_rows(snapshot, self._vendor_trail_cache)
        last_success_age = (
            round(max(0.0, now - self._vendor_trail_last_success_mono), 1)
            if self._vendor_trail_last_success_mono is not None
            else None
        )
        return {
            "enabled": True,
            "mode": "persistent_vendor_cycle",
            "store_version": 1,
            "cycle_owner": "ZoneLedger",
            "current_cycle_key": snapshot.get("vendor_trail_revision"),
            "cycle_ids": {key: row.get("cycle_key") for key, row in self.vendor_trail_store.ledger["zones"].items()},
            "owned_zone_ids": sorted(self.vendor_trail_store.owned_zone_ids()),
            "active_cycle_id": (active_row or {}).get("cycle_id"),
            "poll_interval_s": VENDOR_TRAIL_ACTIVE_TTL_SECONDS,
            "revision": self._vendor_trail_revision,
            "last_fetch_utc": self._vendor_trail_last_fetch_utc,
            "last_success_age_s": last_success_age,
            "last_error": self._vendor_trail_last_error,
            "requested_zone_ids": list(self._vendor_trail_last_zone_ids),
            "current_zone_ids": [row.get("zone_id") for row in current_rows],
            "current_vendor_point_count": sum(
                int(row.get("point_count") or 0) for row in current_rows
            ),
            "active_zone_id": (active_row or {}).get("zone_id"),
            "active_vendor_point_count": int(
                (active_row or {}).get("point_count") or 0
            ),
            "active_vendor_progress": (active_row or {}).get("progress"),
            "backend_tail_authoritative": bool(current_rows),
            "live_tail_allowed": str(snapshot.get("activity") or "").lower() in {"mowing", "paused"},
            **tail_metrics,
        }

    def _map_payload_with_sessions(
        self,
        sessions: list[dict[str, Any]],
        daily_trails: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Expose georeference and source-separated vendor/MQTT trail debug data."""
        payload = super()._map_payload_with_sessions(sessions, daily_trails)
        map_data = payload.get("map") or {}
        payload["georeference"] = (
            map_data.get("georeference") if isinstance(map_data, dict) else None
        )

        active_row = active_vendor_row(self.data or payload, self._vendor_trail_cache)
        backend_segments = payload.get("trail_segments") or []
        if active_row:
            tail_segments = self.vendor_trail_store.live_tail(active_row["zone_id"])
        elif self._vendor_trail_cache and str((self.data or {}).get("activity") or "").lower() in {"docked", "idle", "charging"}:
            tail_segments = []
        elif self._vendor_trail_cache:
            # During a zone transition the unowned zone may use MQTT, but the
            # same session's already-owned zone must not leak back into it.
            from .vendor_trail_render_semantics import filter_current_cycle_source
            active = self.history.active_session or {}
            filtered = filter_current_cycle_source(active, self.vendor_trail_store.owned_zone_ids(), (self.data or {}).get("map", {}).get("zones", []))
            from .history import _card_segments
            tail_segments = _card_segments(filtered)
        else:
            tail_segments = backend_segments
        tail_metrics = {
            "tail_limit_m": None,
            "mqtt_tail_point_count": sum(len(segment) for segment in tail_segments),
            "anchor_xy": tail_segments[-1][-1] if tail_segments else None,
        }
        payload["trail_segments"] = tail_segments
        payload["trail"] = [point for segment in tail_segments for point in segment]
        payload["vendor_trail_debug"] = self._vendor_trail_debug_payload(
            self.data or payload,
            tail_metrics,
        )
        return payload

    def polling_diagnostics(self) -> dict[str, Any]:
        diagnostics = super().polling_diagnostics()
        snapshot = self.data or {}
        active_row = active_vendor_row(snapshot, self._vendor_trail_cache)
        diagnostics["vendor_trail_debug"] = self._vendor_trail_debug_payload(
            snapshot,
            {
                "matched": None,
                "match_distance_m": None,
                "mqtt_tail_point_count": None,
                "mqtt_tail_distance_m": None,
                "anchor_xy": None,
            },
        )
        diagnostics["vendor_trail_debug"]["active_vendor_signature"] = (
            (active_row or {}).get("signature")
        )
        return diagnostics


__all__ = ["NavimowCoordinator", "state_store"]
