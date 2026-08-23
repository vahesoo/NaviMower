"""Narrow semantic corrections layered on the main Navimower coordinator."""
from __future__ import annotations

from typing import Any

from .coordinator import NavimowCoordinator as _BaseNavimowCoordinator
from .coordinator import state_store


def _valid_map_version(value: Any) -> bool:
    """Return whether index2 exposes a usable vendor map revision."""
    return value is not None and str(value).strip() not in {"", "0"}


class NavimowCoordinator(_BaseNavimowCoordinator):
    """Coordinator with strict mowing timestamps and revision-aware map refresh."""

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
        the geometry key when a real version change is observed so the base
        coordinator downloads the new map detail immediately in this poll cycle.
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
        """Attach index2.mapVersion to freshly downloaded map geometry."""
        previous_geometry = self._map_geometry
        super()._maybe_fetch_map(raw)
        if self._map_geometry is None or self._map_geometry is previous_geometry:
            return

        index2 = raw.get("index2")
        map_version = index2.get("mapVersion") if isinstance(index2, dict) else None
        if not _valid_map_version(map_version):
            return
        self._map_geometry["map_version"] = str(map_version)
        base_revision = str(self._map_geometry.get("revision") or "")
        self._map_geometry["revision"] = f"{base_revision}|v:{map_version}"

    @staticmethod
    def _map_snapshot(
        map_geometry: dict[str, Any],
        *,
        cutting_height_supported: bool | None = None,
    ) -> dict[str, Any]:
        """Expose the vendor map revision alongside the existing geometry key."""
        snapshot = _BaseNavimowCoordinator._map_snapshot(
            map_geometry,
            cutting_height_supported=cutting_height_supported,
        )
        snapshot["map_version"] = map_geometry.get("map_version")
        return snapshot


__all__ = ["NavimowCoordinator", "state_store"]
