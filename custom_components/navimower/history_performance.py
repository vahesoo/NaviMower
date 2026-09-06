"""History and current-cycle performance/correctness semantics.

The history store remains the source of truth. This layer separates the zones
used to close an old task from the zones reset by a new command, keeps metadata
queries independent from large point arrays, and gives the backend current-cycle
renderer a complete event signature without rebuilding on every live pose.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from . import current_cycle_render as _current
from . import history as _history
from .session_svg import SESSION_SVG_ARCHIVE_VERSION, build_session_svg_archive
from .zone_state import as_float


def _unique_ints(values: Any) -> list[int]:
    result: list[int] = []
    for raw in values or []:
        try:
            value = int(float(raw))
        except (TypeError, ValueError, OverflowError):
            continue
        if value > 0 and value not in result:
            result.append(value)
    return result


def _cycle_zone_sets(
    active_zone_ids: Any,
    requested_zone_ids: Any,
) -> tuple[list[int], list[int]]:
    """Return ``(closing_zones, reset_zones)`` for one explicit new cycle."""
    active = _unique_ints(active_zone_ids)
    requested = _unique_ints(requested_zone_ids)
    closing = active or requested
    reset = requested or active
    return closing, reset


def _current_cycle_signature(history: Any) -> tuple[Any, ...]:
    """Return a point-light signature for every event that changes the swath."""
    with history._lock:  # noqa: SLF001
        active = history._cache.get(history._active_id or "")  # noqa: SLF001
        reset_zones = tuple(
            _history._unique_ints((active or {}).get("cycle_reset_zone_ids"))  # noqa: SLF001
        )
        reset_set = set(reset_zones)
        visited_reset_zones = tuple(
            zone_id
            for zone_id in _history._unique_ints((active or {}).get("visited_zone_ids"))  # noqa: SLF001
            if zone_id in reset_set
        )
        boundaries = tuple(
            (
                _history._as_int(item.get("zone_id")),  # noqa: SLF001
                _history._as_int(item.get("at_ms")),  # noqa: SLF001
                str(item.get("reason") or ""),
            )
            for item in (active or {}).get("zone_cycle_boundaries") or []
            if isinstance(item, dict)
        )
        completed = tuple(
            (
                str(item.get("id") or ""),
                _history._as_int(item.get("point_count")) or 0,  # noqa: SLF001
                str(item.get("completion_reason") or ""),
                tuple(_history._unique_ints(item.get("cycle_reset_zone_ids"))),  # noqa: SLF001
                tuple(_history._unique_ints(item.get("zone_ids"))),  # noqa: SLF001
                _history._as_int(item.get("segment_count")) or 0,  # noqa: SLF001
            )
            for item in history._sessions  # noqa: SLF001
            if isinstance(item, dict) and not item.get("active")
        )
        return (
            str(history._active_id or ""),  # noqa: SLF001
            tuple(_history._unique_ints(history._force_new_cycle_zone_ids)),  # noqa: SLF001
            reset_zones,
            visited_reset_zones,
            boundaries,
            completed,
        )


def _render_revision(key: tuple[Any, ...], source: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        repr(
            (
                key,
                source.get("zone_ids"),
                source.get("current_cycle_zones"),
                len(source.get("points") or []),
            )
        ).encode("utf-8")
    ).hexdigest()
    return digest[:20]


def install_history_performance() -> None:
    """Install point-light history reads and precise current-cycle rendering."""
    history_cls = _history.NavimowerHistory
    manager_cls = _current.CurrentCycleRenderManager
    if getattr(history_cls, "_history_performance_installed", False):
        return

    original_session_summaries = history_cls.session_summaries
    original_state_key = manager_cls._state_key

    def session_summaries(
        self: Any,
        *,
        include_points: bool = False,
    ) -> list[dict[str, Any]]:
        if include_points:
            return original_session_summaries(self, include_points=True)
        # Metadata already contains the public point_count/segment_count fields.
        # Do not deepcopy the full session cache when no point array was asked for.
        with self._lock:  # noqa: SLF001
            metadata = deepcopy(self._sessions)  # noqa: SLF001
        return [
            _history._card_session(item, include_points=False)  # noqa: SLF001
            for item in metadata
        ]

    def current_cycle_signature(self: Any) -> tuple[Any, ...]:
        return _current_cycle_signature(self)

    def start_new_cycle(
        self: Any,
        *,
        pose_time: Any,
        zone_ids: list[int] | None = None,
        reason: str = "ha_reset_command",
    ) -> bool:
        """Close the old task and reset only the newly requested zone set."""
        boundary_ms = _history._timestamp_ms(pose_time)  # noqa: SLF001
        requested = _history._unique_ints(zone_ids or [])  # noqa: SLF001
        with self._lock:  # noqa: SLF001
            active = self._cache.get(self._active_id or "")  # noqa: SLF001
            active_zones = _history._unique_ints(  # noqa: SLF001
                (active or {}).get("zone_ids") or []
            )
            closing_zones, reset_zones = _cycle_zone_sets(
                active_zones,
                requested,
            )
            final_progress: dict[str, int] = {}
            for zone_id in closing_zones:
                state = self._zone_progress_state.get(str(zone_id)) or {}  # noqa: SLF001
                progress = _history._as_int(state.get("peak_progress"))  # noqa: SLF001
                if progress is None:
                    progress = _history._as_int(state.get("progress"))  # noqa: SLF001
                if progress is not None:
                    final_progress[str(zone_id)] = progress

            confirmed = set(
                _history._unique_ints(  # noqa: SLF001
                    (active or {}).get("task_zone_completion_confirmed")
                )
            )
            completed = bool(closing_zones) and set(closing_zones).issubset(confirmed)
            if active is not None:
                completed = bool(active.get("completed") is True or completed)
                active["completed"] = completed
                active["completion_reason"] = reason
                active["final_progress"] = final_progress
                self._update_active_metadata_locked(active)  # noqa: SLF001
                if self._is_provisional_session(active):  # noqa: SLF001
                    self._discard_active_locked()  # noqa: SLF001
                else:
                    self._finish_active_locked(boundary_ms)  # noqa: SLF001

            self._force_new_session_once = True  # noqa: SLF001
            self._force_new_cycle_zone_ids = list(reset_zones)  # noqa: SLF001
            self._last_cycle_event = {  # noqa: SLF001
                "reason": reason,
                "at_ms": boundary_ms,
                "at": _history._iso(boundary_ms),  # noqa: SLF001
                "zone_ids": list(reset_zones),
                "closed_zone_ids": list(closing_zones),
                "requested_zone_ids": list(requested),
                "completed": completed,
                "final_progress": final_progress,
                "source": "explicit_command",
            }
            self._trail_revision += 1  # noqa: SLF001
        self._schedule_index_save()  # noqa: SLF001
        _history._LOGGER.info(  # noqa: SLF001
            "Started a new Navimower mowing cycle from %s; closed zone(s) %s, reset zone(s) %s",
            reason,
            ", ".join(str(value) for value in closing_zones) or "none",
            ", ".join(str(value) for value in reset_zones) or "all",
        )
        return True

    def state_key(self: Any) -> tuple[Any, ...]:
        return (*original_state_key(self), self.history.current_cycle_signature())

    async def async_get(
        self: Any,
        map_zones: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build a coherent current-cycle artifact outside the HA event loop."""
        key = self._state_key()  # noqa: SLF001
        if key == self._cache_key and self._cache is not None:  # noqa: SLF001
            return deepcopy(self._cache)  # noqa: SLF001

        async with self._lock:  # noqa: SLF001
            attempts = 0
            while True:
                attempts += 1
                key = self._state_key()  # noqa: SLF001
                if key == self._cache_key and self._cache is not None:  # noqa: SLF001
                    return deepcopy(self._cache)  # noqa: SLF001

                sessions = await self.history.async_full_sessions()
                zone_snapshot = deepcopy(map_zones)
                source = await self.coordinator.hass.async_add_executor_job(
                    _current.build_current_cycle_render_source,
                    sessions,
                    zone_snapshot,
                )
                width = as_float(
                    (self.coordinator.data or {}).get("mowing_path_width_m")
                )
                if width is not None and width > 0:
                    source["mowing_path_width_m"] = width

                artifact = None
                if len(source.get("points") or []) >= 2:
                    artifact = await self.coordinator.hass.async_add_executor_job(
                        build_session_svg_archive,
                        source,
                    )

                newest_key = self._state_key()  # noqa: SLF001
                if newest_key != key and attempts < 2:
                    continue

                mowed_area = (
                    deepcopy(artifact.get("mowed_area"))
                    if isinstance(artifact, dict)
                    and isinstance(artifact.get("mowed_area"), dict)
                    else {
                        "path_d": "",
                        "fill_rule": "evenodd",
                        "swath_width_m": width,
                        "grid_size_m": None,
                        "loop_count": 0,
                        "bbox": None,
                    }
                )
                cache_key = newest_key if newest_key == key else key
                result = {
                    "scope": "current_cycle",
                    "revision": _render_revision(cache_key, source),
                    "render_schema_version": (
                        artifact.get("version")
                        if isinstance(artifact, dict)
                        else SESSION_SVG_ARCHIVE_VERSION
                    ),
                    "coordinate_space": "map_xy_m",
                    "zone_ids": list(source.get("zone_ids") or []),
                    "zones": deepcopy(source.get("current_cycle_zones") or []),
                    "source_point_count": len(source.get("points") or []),
                    "mowed_area": mowed_area,
                }

                # A second concurrent state change is rare (the signature does
                # not include normal live points). Return the coherent snapshot
                # but avoid caching it under a newer state key.
                if newest_key == key:
                    self._cache_key = key  # noqa: SLF001
                    self._cache = deepcopy(result)  # noqa: SLF001
                return result

    history_cls.session_summaries = session_summaries
    history_cls.current_cycle_signature = current_cycle_signature
    history_cls.start_new_cycle = start_new_cycle
    manager_cls._state_key = state_key
    manager_cls.async_get = async_get
    history_cls._history_performance_installed = True
