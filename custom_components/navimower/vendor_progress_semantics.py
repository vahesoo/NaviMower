"""Vendor-current reset protection for History.

CycleEngine owns current per-zone publication. This module only filters a single
uncorroborated hard vendor drop before History decides that a new cycle began.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import history as _history

_RESET_CANDIDATE_MAX_AGE_MS = 60_000
_RESET_CONFIRMATIONS_REQUIRED = 2
_RESET_LOW_PROGRESS_MAX = 25
_RESET_LIVE_CORROBORATION_MAX = 30


def _coverage_rows(snapshot: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    coverage = snapshot.get("coverage")
    rows = coverage.get("zones") if isinstance(coverage, dict) else None
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        zone_id = _history._as_int(item.get("id"))  # noqa: SLF001
        if zone_id is not None:
            result[zone_id] = item
    return result


def _hard_reset_drop(old_peak: int | None, new_progress: int | None) -> bool:
    if old_peak is None or new_progress is None:
        return False
    drop = old_peak - new_progress
    return bool(
        (old_peak >= 15 and new_progress <= 5 and drop >= 15)
        or (old_peak >= 50 and new_progress <= 20 and drop >= 30)
    )


def _timestamp_reset(
    old_progress: int | None,
    new_progress: int | None,
    old_start: int | None,
    new_start: int | None,
) -> bool:
    return bool(
        old_progress is not None
        and new_progress is not None
        and old_start is not None
        and new_start is not None
        and new_start > old_start
        and new_progress <= _RESET_LOW_PROGRESS_MAX
        and old_progress - new_progress >= 10
    )


def _filtered_reset_snapshot(
    owner: Any,
    snapshot: dict[str, Any],
    pose_time: Any,
) -> dict[str, Any]:
    """Suppress one uncorroborated hard drop before history sees a reset.

    A newer vendor start timestamp is strong reset evidence and passes
    immediately. Without it, a hard drop must repeat. When fresh active-zone
    work progress exists and is still high, the low coverage sample is treated
    as stale and does not advance the confirmation counter.
    """
    active = owner._cache.get(owner._active_id or "")  # noqa: SLF001
    if not isinstance(active, dict):
        return snapshot

    rows = _coverage_rows(snapshot)
    if not rows:
        return snapshot

    owner_zone_id = _history._as_int(snapshot.get("active_zone_progress_zone_id"))  # noqa: SLF001
    live_progress = _history._as_int(snapshot.get("active_zone_progress"))  # noqa: SLF001
    now_ms = _history._timestamp_ms(pose_time)  # noqa: SLF001
    candidates = getattr(owner, "_vendor_reset_candidates", None)
    if not isinstance(candidates, dict):
        candidates = {}
        owner._vendor_reset_candidates = candidates  # noqa: SLF001

    filtered: dict[str, Any] | None = None
    filtered_rows: list[dict[str, Any]] | None = None

    for zone_id, row in rows.items():
        if owner_zone_id is not None and zone_id != owner_zone_id:
            continue
        previous = dict(owner._zone_progress_state.get(str(zone_id)) or {})  # noqa: SLF001
        old_progress = _history._as_int(previous.get("progress"))  # noqa: SLF001
        old_peak = _history._as_int(previous.get("peak_progress"))  # noqa: SLF001
        if old_peak is None:
            old_peak = old_progress
        new_progress = _history._as_int(row.get("pct"))  # noqa: SLF001
        old_start = _history._as_int(previous.get("start_time"))  # noqa: SLF001
        new_start = _history._as_int(row.get("start_time"))  # noqa: SLF001
        key = str(zone_id)

        if _timestamp_reset(old_progress, new_progress, old_start, new_start):
            candidates.pop(key, None)
            continue
        if not _hard_reset_drop(old_peak, new_progress):
            if new_progress is not None and old_peak is not None and new_progress >= old_peak:
                candidates.pop(key, None)
            continue

        # If the dense active-zone counter clearly says work is already well
        # beyond the alleged reset, do not count this low coverage sample toward
        # reset confirmation.
        live_corroborates = bool(
            owner_zone_id == zone_id
            and live_progress is not None
            and live_progress <= _RESET_LIVE_CORROBORATION_MAX
        )
        live_contradicts = bool(
            owner_zone_id == zone_id
            and live_progress is not None
            and live_progress > _RESET_LIVE_CORROBORATION_MAX
        )

        candidate = dict(candidates.get(key) or {})
        first_ms = _history._as_int(candidate.get("first_ms"))  # noqa: SLF001
        same_cycle_hint = candidate.get("start_time") == new_start
        recent = bool(
            first_ms is not None
            and 0 <= now_ms - first_ms <= _RESET_CANDIDATE_MAX_AGE_MS
        )
        count = _history._as_int(candidate.get("count")) or 0  # noqa: SLF001
        if live_contradicts:
            count = 0
        elif recent and same_cycle_hint:
            count += 1
        else:
            count = 1
            first_ms = now_ms

        candidates[key] = {
            "first_ms": first_ms,
            "last_ms": now_ms,
            "count": count,
            "start_time": new_start,
            "progress": new_progress,
            "previous_peak": old_peak,
            "live_progress": live_progress,
            "live_corroborates": live_corroborates,
        }

        if count >= _RESET_CONFIRMATIONS_REQUIRED and not live_contradicts:
            candidates.pop(key, None)
            continue

        # First/unconfirmed low sample: let history observe the previous vendor
        # progress so it does not create a false cycle boundary. The original
        # snapshot remains unchanged and is still available to diagnostics.
        if filtered is None:
            filtered = deepcopy(snapshot)
            coverage = filtered.get("coverage")
            filtered_rows = coverage.get("zones") if isinstance(coverage, dict) else None
        if filtered_rows is None:
            continue
        for mutable in filtered_rows:
            if not isinstance(mutable, dict):
                continue
            if _history._as_int(mutable.get("id")) != zone_id:  # noqa: SLF001
                continue
            hold = old_progress if old_progress is not None else old_peak
            if hold is not None:
                mutable["pct"] = hold
            break

    return filtered or snapshot


def install_vendor_progress_semantics() -> None:
    """Install only the History reset guard used before CycleEngine reduction."""
    history_cls = _history.NavimowerHistory
    if getattr(history_cls, "_vendor_progress_semantics_installed", False):
        return

    original_prepare_cycle = history_cls.prepare_cycle

    def prepare_cycle(
        self: Any,
        snapshot: dict[str, Any],
        *,
        pose_time: Any,
    ) -> bool:
        filtered = _filtered_reset_snapshot(self, snapshot, pose_time)
        return original_prepare_cycle(self, filtered, pose_time=pose_time)

    history_cls.prepare_cycle = prepare_cycle
    history_cls._vendor_progress_semantics_installed = True
