"""Narrow semantic corrections layered on the main Navimower coordinator."""
from __future__ import annotations

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
    validate_georeference,
)
from .map_identifiers import resolve_map_identifiers


def _valid_map_version(value: Any) -> bool:
    """Return whether index2 exposes a usable vendor map revision."""
    return value is not None and str(value).strip() not in {"", "0"}


class NavimowCoordinator(_BaseNavimowCoordinator):
    """Coordinator with strict mowing timestamps and revision-aware map refresh."""

    async def async_load_persistent_state(self) -> None:
        """Restore state and force one map refresh for pre-georeference caches."""
        await super().async_load_persistent_state()
        if self._map_geometry is not None and not self._map_geometry.get("georeference"):
            # v0.4.3 caches contain perfectly usable local geometry but not the
            # WGS84 tie point needed by multi-mower/site views. Keep displaying
            # the cached map immediately, then re-decode it on the first poll.
            self._map_cache_key = None

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

    def _maybe_fetch_map(self, raw: dict[str, Any]) -> None:
        """Decode map geometry plus its WGS84 georeference in one cloud fetch."""
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
        georeference: dict[str, Any] | None = None
        try:
            plain = self.client.map_detail_plain(
                self.sn, str(map_id), str(map_base_id)
            )
            geometry = _parse_map_detail_plain(plain)
            georeference = georeference_from_plain_map_detail(plain)
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
                georeference = georeference_from_compressed_map_detail(blob)
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
        if georeference is not None:
            geometry["georeference"] = georeference

        index2 = raw.get("index2")
        map_version = index2.get("mapVersion") if isinstance(index2, dict) else None
        if _valid_map_version(map_version):
            geometry["map_version"] = str(map_version)
            geometry["revision"] = f"{geometry['revision']}|v:{map_version}"

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
        """Attach passive XY/GPS validation to the normalized georeference."""
        snapshot = super()._parse(raw)
        base_georeference = (self._map_geometry or {}).get("georeference")
        georeference = validate_georeference(
            base_georeference,
            raw.get("location"),
        )
        snapshot["georeference"] = georeference
        map_data = snapshot.get("map")
        if isinstance(map_data, dict):
            map_data = dict(map_data)
            map_data["georeference"] = georeference
            snapshot["map"] = map_data
        return snapshot

    def _map_payload_with_sessions(
        self,
        sessions: list[dict[str, Any]],
        daily_trails: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Expose georeference both with the map and as small top-level metadata."""
        payload = super()._map_payload_with_sessions(sessions, daily_trails)
        map_data = payload.get("map") or {}
        payload["georeference"] = (
            map_data.get("georeference") if isinstance(map_data, dict) else None
        )
        return payload


__all__ = ["NavimowCoordinator", "state_store"]
