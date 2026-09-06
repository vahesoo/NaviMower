"""Stable semantics for per-zone completion timestamps.

`last_completed_at` is historical state: once a newer confirmed completion has
been published it must never move backwards because a stale vendor cycle arrives
later. This layer also narrows the legacy startup repair so a modern completion
confirmed from fresh per-zone coverage is not deleted merely because it is close
to a reset boundary.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import history as _history


_COMPLETION_FIELDS = (
    "last_completed_at",
    "last_completed_progress",
    "last_completed_source",
    "last_completed_confirmation",
    "last_completed_cycle_id",
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


def install_completion_semantics() -> None:
    """Install monotonic completion publication and conservative startup repair."""
    cls = _history.NavimowerHistory
    if getattr(cls, "_completion_semantics_installed", False):
        return

    original_confirm = cls._confirm_coverage_completions_locked

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

        if rejected:
            self._schedule_index_save()  # noqa: SLF001
            for rejection in rejected:
                _history._LOGGER.info(  # noqa: SLF001
                    "Rejected older Navimower completion for zone %s: candidate %s < persisted %s",
                    rejection.get("zone_id"),
                    rejection.get("candidate_at"),
                    rejection.get("persisted_at"),
                )

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
    cls._async_repair_unverified_zone_completions = repair_unverified_zone_completions
    cls._completion_semantics_installed = True
