"""Vendor-first per-zone progress publication with narrow reset protection.

Private ``get-path-info-time`` coverage is the canonical per-zone numeric state.
The integration may retain a newer value when an older/stale vendor snapshot
regresses inside the same mowing cycle, but it must not derive a competing
percentage from MQTT/session bookkeeping. MQTT work progress remains useful for
active-zone context and reset corroboration only.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import coordinator as _coordinator
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


def _apply_vendor_first_zone_state(owner: Any, snapshot: dict[str, Any]) -> None:
    """Publish canonical vendor coverage, holding only stale same-cycle drops."""
    rows = snapshot.get("zone_states")
    if not isinstance(rows, list):
        return
    coverage_by_id = _coverage_rows(snapshot)
    diagnostics = owner.history.cycle_diagnostics()
    progress_state = diagnostics.get("zone_progress_state") or {}
    changed = False

    for row in rows:
        if not isinstance(row, dict):
            continue
        zone_id = _history._as_int(row.get("id"))  # noqa: SLF001
        if zone_id is None:
            continue
        vendor = coverage_by_id.get(zone_id)
        if not isinstance(vendor, dict):
            continue
        raw_pct = _history._as_float(vendor.get("pct"))  # noqa: SLF001
        if raw_pct is None or not 0 <= raw_pct <= 100:
            continue

        state = progress_state.get(str(zone_id)) or {}
        peak = _history._as_float(state.get("peak_progress"))  # noqa: SLF001
        accepted = raw_pct
        held = bool(peak is not None and peak > raw_pct)
        if held:
            accepted = min(100.0, peak)

        row["vendor_coverage_pct"] = round(raw_pct, 1)
        row["coverage_pct"] = round(accepted, 1)
        area = _history._as_float(row.get("area_m2"))  # noqa: SLF001
        vendor_finished = _history._as_float(vendor.get("finished"))  # noqa: SLF001
        if held:
            row["mowed_area_m2"] = (
                round(area * accepted / 100.0, 2) if area is not None else None
            )
            row["progress_source"] = "vendor_coverage_monotonic_hold"
            row["progress_guard"] = True
            row["progress_guard_reason"] = "same_cycle_vendor_regression"
            row["progress_guard_vendor_pct"] = round(raw_pct, 1)
        else:
            if vendor_finished is not None and vendor_finished >= 0:
                row["mowed_area_m2"] = (
                    round(min(area, vendor_finished), 2)
                    if area is not None
                    else round(vendor_finished, 2)
                )
            elif area is not None:
                row["mowed_area_m2"] = round(area * accepted / 100.0, 2)
            row["progress_source"] = "vendor_coverage"
            row.pop("progress_guard", None)
            row.pop("progress_guard_reason", None)
            row.pop("progress_guard_vendor_pct", None)
        changed = True

    if not changed:
        return

    known = [
        row
        for row in rows
        if isinstance(row, dict) and _history._as_float(row.get("area_m2")) is not None  # noqa: SLF001
    ]
    map_area = sum(float(row.get("area_m2") or 0.0) for row in known)
    map_mowed = sum(float(row.get("mowed_area_m2") or 0.0) for row in known)
    totals = snapshot.get("totals")
    if isinstance(totals, dict):
        totals["map_area_m2"] = round(map_area, 2) if map_area > 0 else None
        totals["map_mowed_area_m2"] = round(map_mowed, 2) if map_area > 0 else None
        totals["map_coverage_pct"] = (
            round(100.0 * map_mowed / map_area, 1) if map_area > 0 else None
        )
        totals["completed_zone_count"] = sum(
            1
            for row in rows
            if (_history._as_float((row or {}).get("coverage_pct")) or 0) >= 95  # noqa: SLF001
        )
    snapshot["zone_progress_policy"] = "vendor_first_monotonic_with_confirmed_reset"


def install_vendor_progress_semantics() -> None:
    """Install vendor-first publication after completion semantics."""
    history_cls = _history.NavimowerHistory
    coordinator_cls = _coordinator.NavimowCoordinator
    if getattr(history_cls, "_vendor_progress_semantics_installed", False):
        return

    original_prepare_cycle = history_cls.prepare_cycle
    original_refresh_zone_model = coordinator_cls._refresh_zone_model

    def prepare_cycle(
        self: Any,
        snapshot: dict[str, Any],
        *,
        pose_time: Any,
    ) -> bool:
        filtered = _filtered_reset_snapshot(self, snapshot, pose_time)
        return original_prepare_cycle(self, filtered, pose_time=pose_time)

    def refresh_zone_model(self: Any, snapshot: dict[str, Any]) -> None:
        original_refresh_zone_model(self, snapshot)
        _apply_vendor_first_zone_state(self, snapshot)

    history_cls.prepare_cycle = prepare_cycle
    coordinator_cls._refresh_zone_model = refresh_zone_model
    history_cls._vendor_progress_semantics_installed = True
