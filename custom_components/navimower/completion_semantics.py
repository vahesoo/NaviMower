"""Stable semantics for per-zone completion timestamps and completed coverage.

`last_completed_at` is historical state: once a newer confirmed completion has
been published it must never move backwards because a stale vendor cycle arrives
later. This layer also narrows the legacy startup repair so a modern completion
confirmed from fresh per-zone coverage is not deleted merely because it is close
to a reset boundary.

A verified 100% zone is also protected from a later vendor coverage regression
when that zone's own geometry is unchanged and no newer mowing cycle/reset has
been observed for it. Navimow can recalculate mowing progress after unrelated map
edits; that must not turn an already completed, unchanged zone into a partial one.
The raw vendor percentage remains exposed separately for diagnostics.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from . import history as _history


_COMPLETION_FIELDS = (
    "last_completed_at",
    "last_completed_progress",
    "last_completed_source",
    "last_completed_confirmation",
    "last_completed_cycle_id",
    "last_completed_area_m2",
    "last_completed_geometry_signature",
)
_VERIFIED_COMPLETION_SOURCE = "private_zone_coverage"
_VERIFIED_CONFIRMATION_PREFIX = "coverage_100_"


def _verified_completion(record: dict[str, Any] | None) -> bool:
    """Return whether a persisted completion came from the strict coverage path."""
    row = record or {}
    return bool(
        _history._as_int(row.get("last_completed_progress")) == 100  # noqa: SLF001
        and str(row.get("last_completed_source") or "")
        == _VERIFIED_COMPLETION_SOURCE
        and str(row.get("last_completed_confirmation") or "").startswith(
            _VERIFIED_CONFIRMATION_PREFIX
        )
        and _history._iso_ms(row.get("last_completed_at")) is not None  # noqa: SLF001
    )


def _completion_regression(
    previous: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Describe a strict timestamp regression, otherwise return ``None``."""
    old = previous or {}
    new = candidate or {}
    old_ms = _history._iso_ms(old.get("last_completed_at"))  # noqa: SLF001
    new_ms = _history._iso_ms(new.get("last_completed_at"))  # noqa: SLF001
    if old_ms is None or new_ms is None or new_ms >= old_ms:
        return None
    return {
        "reason": "older_than_persisted_completion",
        "candidate_at": new.get("last_completed_at"),
        "persisted_at": old.get("last_completed_at"),
        "candidate_source": new.get("last_completed_source"),
        "candidate_confirmation": new.get("last_completed_confirmation"),
        "candidate_cycle_id": new.get("last_completed_cycle_id"),
        "persisted_source": old.get("last_completed_source"),
        "persisted_confirmation": old.get("last_completed_confirmation"),
        "persisted_cycle_id": old.get("last_completed_cycle_id"),
    }


def _preserve_newest_completion(
    previous: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return candidate state unless it would move Last completed backwards."""
    old = dict(previous or {})
    new = dict(candidate or {})
    rejection = _completion_regression(old, new)
    if rejection is None:
        return new, None
    for key in _COMPLETION_FIELDS:
        if key in old:
            new[key] = deepcopy(old[key])
        else:
            new.pop(key, None)
    new["last_completion_rejection"] = deepcopy(rejection)
    return new, rejection


def _near_reset_boundary(
    completed_ms: int,
    zone_id: int | None,
    reset_boundaries: dict[int, list[int]],
) -> bool:
    if zone_id is None:
        return False
    return any(
        abs(completed_ms - boundary) <= 60_000
        for boundary in reset_boundaries.get(zone_id, [])
    )


def _should_repair_legacy_completion(
    record: dict[str, Any],
    *,
    zone_id: int | None,
    reset_boundaries: dict[int, list[int]],
) -> bool:
    """Repair only completion rows that do not carry modern strict evidence."""
    completed_ms = _history._iso_ms(record.get("last_completed_at"))  # noqa: SLF001
    if completed_ms is None:
        return False
    if _verified_completion(record):
        return False
    unverified = _history._as_int(record.get("last_completed_progress")) is None  # noqa: SLF001
    return bool(
        unverified
        or _near_reset_boundary(completed_ms, zone_id, reset_boundaries)
    )


def _canonical_polygon(polygon: Any) -> tuple[tuple[float, float], ...] | None:
    """Return a rotation/direction independent local-X/Y polygon fingerprint input."""
    if not isinstance(polygon, list):
        return None
    points: list[tuple[float, float]] = []
    for raw in polygon:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            return None
        x = _history._as_float(raw[0])  # noqa: SLF001
        y = _history._as_float(raw[1])  # noqa: SLF001
        if x is None or y is None:
            return None
        points.append((round(x, 3), round(y, 3)))
    if len(points) >= 2 and points[0] == points[-1]:
        points.pop()
    if len(points) < 3:
        return None

    sequence = tuple(points)
    reverse = tuple(reversed(points))
    variants: list[tuple[tuple[float, float], ...]] = []
    for source in (sequence, reverse):
        for index in range(len(source)):
            variants.append(source[index:] + source[:index])
    return min(variants)


def _zone_geometry_signature(zone: dict[str, Any] | None) -> str | None:
    """Hash only zone-local geometry/area, not the global map revision."""
    row = zone or {}
    area = _history._as_float(  # noqa: SLF001
        row.get("area") if row.get("area") is not None else row.get("area_m2")
    )
    polygon = _canonical_polygon(row.get("polygon"))
    if area is None or area < 0 or polygon is None:
        return None
    payload = "{:.2f}|{}".format(
        round(area, 2),
        ";".join(f"{x:.3f},{y:.3f}" for x, y in polygon),
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _map_zones_by_id(map_zones: Any) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in map_zones or []:
        if not isinstance(item, dict):
            continue
        zone_id = _history._as_int(item.get("id"))  # noqa: SLF001
        if zone_id is not None:
            result[zone_id] = item
    return result


def _coverage_rows_by_id(coverage: Any) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    rows = coverage.get("zones") if isinstance(coverage, dict) else None
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        zone_id = _history._as_int(item.get("id"))  # noqa: SLF001
        if zone_id is not None:
            result[zone_id] = item
    return result


def _reset_after_completion(
    active_session: dict[str, Any] | None,
    zone_id: int,
    record: dict[str, Any],
) -> bool:
    """Return whether session evidence proves a newer cycle for this zone."""
    session = active_session if isinstance(active_session, dict) else {}
    if not session:
        return False

    completed_ms = _history._iso_ms(record.get("last_completed_at"))  # noqa: SLF001
    completed_cycle = str(record.get("last_completed_cycle_id") or "")
    session_id = str(session.get("id") or "")
    visited = {
        value
        for value in (
            _history._as_int(item)  # noqa: SLF001
            for item in session.get("visited_zone_ids") or []
        )
        if value is not None
    }

    # A zone entered in a different retained session is unambiguously a new
    # cycle, even before its vendor percentage has advanced from zero.
    if zone_id in visited and session_id and session_id != completed_cycle:
        return True

    for item in session.get("zone_cycle_boundaries") or []:
        if not isinstance(item, dict):
            continue
        if _history._as_int(item.get("zone_id")) != zone_id:  # noqa: SLF001
            continue
        boundary_ms = _history._as_int(item.get("at_ms"))  # noqa: SLF001
        if boundary_ms is None:
            boundary_ms = _history._iso_ms(item.get("at"))  # noqa: SLF001
        if boundary_ms is not None and (
            completed_ms is None or boundary_ms > completed_ms
        ):
            return True

    reset_zone_ids = {
        value
        for value in (
            _history._as_int(item)  # noqa: SLF001
            for item in session.get("cycle_reset_zone_ids") or []
        )
        if value is not None
    }
    if zone_id in reset_zone_ids:
        session_started_ms = _history._as_int(session.get("started_at_ms"))  # noqa: SLF001
        if session_started_ms is None:
            session_started_ms = _history._iso_ms(session.get("started_at"))  # noqa: SLF001
        if completed_ms is None or (
            session_started_ms is not None and session_started_ms > completed_ms
        ):
            return True

    return False


def _backfill_completed_geometry(self: Any, snapshot: dict[str, Any]) -> bool:
    """Persist a safe geometry baseline only while vendor still reports 100%."""
    map_data = snapshot.get("map") if isinstance(snapshot, dict) else None
    map_by_id = _map_zones_by_id(
        map_data.get("zones") if isinstance(map_data, dict) else None
    )
    coverage_by_id = _coverage_rows_by_id(
        snapshot.get("coverage") if isinstance(snapshot, dict) else None
    )
    if not map_by_id or not coverage_by_id:
        return False

    changed = False
    with self._lock:  # noqa: SLF001
        for key, original in list(self._zone_history.items()):  # noqa: SLF001
            if not isinstance(original, dict) or not _verified_completion(original):
                continue
            zone_id = _history._as_int(original.get("id")) or _history._as_int(key)  # noqa: SLF001
            if zone_id is None:
                continue
            coverage_row = coverage_by_id.get(zone_id) or {}
            if _history._as_int(coverage_row.get("pct")) != 100:  # noqa: SLF001
                continue
            map_zone = map_by_id.get(zone_id)
            signature = _zone_geometry_signature(map_zone)
            if signature is None:
                continue
            area = _history._as_float((map_zone or {}).get("area"))  # noqa: SLF001
            record = dict(original)
            if (
                record.get("last_completed_geometry_signature") == signature
                and _history._as_float(record.get("last_completed_area_m2")) == area  # noqa: SLF001
            ):
                continue
            record["last_completed_geometry_signature"] = signature
            record["last_completed_area_m2"] = area
            self._zone_history[str(key)] = record  # noqa: SLF001
            changed = True
    if changed:
        self._schedule_index_save()  # noqa: SLF001
    return changed


def _apply_completed_coverage_hold(
    rows: list[dict[str, Any]],
    totals: dict[str, Any],
    *,
    map_zones: list[dict[str, Any]],
    zone_history: dict[str, dict[str, Any]],
    active_session: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep verified completed zones at 100 across unrelated map recalculation."""
    map_by_id = _map_zones_by_id(map_zones)
    changed = False

    for row in rows:
        if not isinstance(row, dict):
            continue
        zone_id = _history._as_int(row.get("id"))  # noqa: SLF001
        if zone_id is None:
            continue
        record = zone_history.get(str(zone_id)) or {}
        if not _verified_completion(record):
            continue

        vendor_pct = _history._as_float(row.get("vendor_coverage_pct"))  # noqa: SLF001
        display_pct = _history._as_float(row.get("coverage_pct"))  # noqa: SLF001
        if (
            vendor_pct is None
            or vendor_pct >= 100
            or display_pct is None
            or display_pct >= 100
        ):
            continue

        stored_signature = str(
            record.get("last_completed_geometry_signature") or ""
        )
        current_signature = _zone_geometry_signature(map_by_id.get(zone_id))
        if not stored_signature or current_signature != stored_signature:
            continue
        if _reset_after_completion(active_session, zone_id, record):
            continue

        area = _history._as_float(row.get("area_m2"))  # noqa: SLF001
        if area is None or area < 0:
            continue

        row["coverage_pct"] = 100.0
        row["mowed_area_m2"] = round(area, 2)
        row["progress_source"] = "verified_completion_hold"
        row["completion_hold"] = True
        row["completion_hold_reason"] = "verified_100_same_geometry_no_new_cycle"
        row["completion_hold_vendor_pct"] = round(vendor_pct, 1)
        changed = True

    if not changed:
        return rows, totals

    map_area = sum(
        float(row.get("area_m2") or 0.0)
        for row in rows
        if row.get("area_m2") is not None
    )
    map_mowed = sum(
        float(row.get("mowed_area_m2") or 0.0)
        for row in rows
        if row.get("area_m2") is not None
    )
    totals["map_area_m2"] = round(map_area, 2) if map_area > 0 else None
    totals["map_mowed_area_m2"] = round(map_mowed, 2) if map_area > 0 else None
    totals["map_coverage_pct"] = (
        round(100.0 * map_mowed / map_area, 1) if map_area > 0 else None
    )
    totals["completed_zone_count"] = sum(
        1
        for row in rows
        if (_history._as_float(row.get("coverage_pct")) or 0) >= 95  # noqa: SLF001
    )
    return rows, totals


def install_completion_semantics() -> None:
    """Install monotonic completion publication and completed-zone coverage safety."""
    cls = _history.NavimowerHistory
    if getattr(cls, "_completion_semantics_installed", False):
        return

    original_confirm = cls._confirm_coverage_completions_locked
    original_update_from_snapshot = cls.update_from_snapshot

    def confirm_coverage_completions_locked(
        self: Any,
        snapshot: dict[str, Any],
        pose_time: Any,
    ) -> None:
        with self._lock:  # noqa: SLF001
            before = deepcopy(self._zone_history)  # noqa: SLF001
        original_confirm(self, snapshot, pose_time)

        rejected: list[dict[str, Any]] = []
        with self._lock:  # noqa: SLF001
            for zone_key, candidate in list(self._zone_history.items()):  # noqa: SLF001
                previous = before.get(str(zone_key))
                if not isinstance(candidate, dict) or not isinstance(previous, dict):
                    continue
                protected, rejection = _preserve_newest_completion(
                    previous,
                    candidate,
                )
                if rejection is None:
                    continue
                zone_id = _history._as_int(protected.get("id")) or _history._as_int(zone_key)  # noqa: SLF001
                rejection = {**rejection, "zone_id": zone_id}
                protected["last_completion_rejection"] = deepcopy(rejection)
                self._zone_history[str(zone_key)] = protected  # noqa: SLF001
                rejected.append(rejection)

            if rejected:
                active = self._cache.get(self._active_id or "")  # noqa: SLF001
                if isinstance(active, dict):
                    # Preserve current-cycle completion confirmation: only the
                    # historical timestamp candidate was stale. Diagnostics make
                    # the rejected vendor evidence visible for field reports.
                    active["last_completion_rejection"] = deepcopy(rejected[-1])
                    self._update_active_metadata_locked(active)  # noqa: SLF001
                    self._schedule_active_save()  # noqa: SLF001

        _backfill_completed_geometry(self, snapshot)

        if rejected:
            self._schedule_index_save()  # noqa: SLF001
            for rejection in rejected:
                _history._LOGGER.info(  # noqa: SLF001
                    "Rejected older Navimower completion for zone %s: candidate %s < persisted %s",
                    rejection.get("zone_id"),
                    rejection.get("candidate_at"),
                    rejection.get("persisted_at"),
                )

    def update_from_snapshot(self: Any, snapshot: dict[str, Any]) -> None:
        original_update_from_snapshot(self, snapshot)
        # Upgrades already have verified completion history but no geometry
        # fingerprint. Seed it only while the vendor currently re-affirms 100%,
        # never from an already-regressed 89/0% sample.
        _backfill_completed_geometry(self, snapshot)

    async def repair_unverified_zone_completions(self: Any) -> None:
        """Remove only legacy/unverified completion timestamps at startup."""
        repaired = 0
        with self._lock:  # noqa: SLF001
            reset_boundaries: dict[int, list[int]] = {}
            for session in self._cache.values():  # noqa: SLF001
                for item in (session or {}).get("zone_cycle_boundaries") or []:
                    if not isinstance(item, dict):
                        continue
                    zone_id = _history._as_int(item.get("zone_id"))  # noqa: SLF001
                    at_ms = _history._as_int(item.get("at_ms"))  # noqa: SLF001
                    if zone_id is not None and at_ms is not None:
                        reset_boundaries.setdefault(zone_id, []).append(at_ms)

            for key, original in list(self._zone_history.items()):  # noqa: SLF001
                record = dict(original)
                zone_id = _history._as_int(record.get("id")) or _history._as_int(key)  # noqa: SLF001
                if not _should_repair_legacy_completion(
                    record,
                    zone_id=zone_id,
                    reset_boundaries=reset_boundaries,
                ):
                    continue
                removed = {
                    field: record.get(field)
                    for field in _COMPLETION_FIELDS
                    if record.get(field) is not None
                }
                for field in _COMPLETION_FIELDS:
                    record.pop(field, None)
                record["last_completion_repair"] = {
                    "reason": "legacy_unverified_completion",
                    "removed": removed,
                }
                self._zone_history[str(key)] = record  # noqa: SLF001
                repaired += 1

        if repaired:
            await self._index_store.async_save(self._index_data())  # noqa: SLF001
            _history._LOGGER.info(  # noqa: SLF001
                "Removed %d legacy/unverified Navimower zone completion timestamp(s)",
                repaired,
            )

    cls._confirm_coverage_completions_locked = confirm_coverage_completions_locked
    cls.update_from_snapshot = update_from_snapshot
    cls._async_repair_unverified_zone_completions = repair_unverified_zone_completions

    # coordinator.py imported build_zone_model directly, so wrap that exact
    # production reference rather than changing the generic zone_state helper.
    # This keeps unit callers deterministic while every HA snapshot gets the
    # completed-zone hold before sensor/map publication.
    from . import coordinator as _coordinator

    original_build_zone_model = _coordinator.build_zone_model

    def guarded_build_zone_model(*args: Any, **kwargs: Any):
        rows, totals = original_build_zone_model(*args, **kwargs)
        return _apply_completed_coverage_hold(
            rows,
            totals,
            map_zones=kwargs.get("map_zones") or [],
            zone_history=kwargs.get("zone_history") or {},
            active_session=kwargs.get("active_session"),
        )

    _coordinator.build_zone_model = guarded_build_zone_model
    cls._completion_semantics_installed = True
