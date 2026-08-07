"""Runtime compatibility fixes for vendor telemetry that varies by mower generation.

This module keeps model-specific wire quirks out of entity code.  It normalizes
cutting-height capability, assigns live work progress to its actual work-zone,
and records inferred per-zone mowing-cycle boundaries without fragmenting one
logical mowing session.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import coordinator as coordinator_module
from . import history as history_module
from .const import VENDOR_COMPLETION_PROGRESS_MIN

_INSTALLED = False

_ORIGINAL_PARSE = coordinator_module.NavimowCoordinator._parse
_ORIGINAL_METADATA = history_module._metadata
_ORIGINAL_MERGE_SESSION_RECORDS = history_module._merge_session_records
_ORIGINAL_SESSIONS_CAN_MERGE = history_module._sessions_can_merge
_ORIGINAL_UPDATE_FROM_SNAPSHOT = history_module.NavimowerHistory.update_from_snapshot


def _valid_swath_width_m(raw: Any) -> float | None:
    parsed = coordinator_module._as_float(raw)
    if parsed is None:
        return None
    # Device-info reports mowingPathWidth in millimetres (400 => 0.40 m).
    width = parsed / 1000.0
    return width if 0.10 <= width <= 2.0 else None


def _supported_height_values(device_info: dict[str, Any]) -> list[int]:
    values = device_info.get("mowingHeightList") or []
    if not isinstance(values, list):
        return []
    result: list[int] = []
    for value in values:
        normalized = coordinator_module._normalize_cutting_height_mm(value)
        if normalized is not None and normalized not in result:
            result.append(normalized)
    return result


def _repair_cutting_height_snapshot(
    coordinator: coordinator_module.NavimowCoordinator,
    raw: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    """Expose usable heights even when one zone carries opaque vendor encoding.

    Values such as 316 are deliberately *not* decoded.  When the mower reports a
    valid global height and a supported-height list, an undecodable per-zone raw
    value simply has no usable explicit override and falls back to the global
    height for display.  ``inherits_global_height`` remains ``None`` for that
    case so the integration does not pretend to know what the opaque marker
    means.
    """
    device_info = raw.get("device_info") or {}
    if not isinstance(device_info, dict):
        return
    supported = _supported_height_values(device_info)
    global_height = coordinator_module._normalize_cutting_height_mm(
        (snapshot.get("settings") or {}).get("cut_height")
        if isinstance(snapshot.get("settings"), dict)
        else snapshot.get("cutting_height_mm")
    )
    if not supported or global_height is None:
        return

    snapshot["cutting_height_supported"] = True
    snapshot["cut_height"] = global_height
    snapshot["cutting_height_mm"] = global_height
    settings = dict(snapshot.get("settings") or {})
    settings["cut_height"] = global_height
    settings["cutting_height_supported"] = True
    snapshot["settings"] = settings

    details: list[dict[str, Any]] = []
    for item in snapshot.get("zone_details") or []:
        if not isinstance(item, dict):
            continue
        detail = dict(item)
        raw_height = coordinator_module._as_int(detail.get("configured_height_raw"))
        normalized = coordinator_module._normalize_cutting_height_mm(raw_height)
        if raw_height in (None, 0, 256):
            detail["configured_height_mm"] = None
            detail["cutting_height_mm"] = global_height
            detail["inherits_global_height"] = True
        elif normalized is not None:
            detail["configured_height_mm"] = normalized
            detail["cutting_height_mm"] = normalized
            detail["inherits_global_height"] = False
        else:
            # Preserve the opaque raw marker for diagnostics.  Do not decode it.
            detail["configured_height_mm"] = None
            detail["cutting_height_mm"] = global_height
            detail["inherits_global_height"] = None
        detail["cutting_height_supported"] = True
        details.append(detail)
    snapshot["zone_details"] = details

    if coordinator._map_geometry:
        snapshot["map"] = coordinator._map_snapshot(
            coordinator._map_geometry,
            cutting_height_supported=True,
        )


def _parse_with_compat(
    self: coordinator_module.NavimowCoordinator,
    raw: dict[str, Any],
) -> dict[str, Any]:
    snapshot = _ORIGINAL_PARSE(self, raw)
    _repair_cutting_height_snapshot(self, raw, snapshot)

    device_info = raw.get("device_info") or {}
    mowing_extend = (
        device_info.get("mowingExtend")
        if isinstance(device_info, dict)
        and isinstance(device_info.get("mowingExtend"), dict)
        else {}
    )
    swath = _valid_swath_width_m((mowing_extend or {}).get("mowingPathWidth"))
    if swath is not None:
        snapshot["mowing_path_width_m"] = swath
    return snapshot


def _metadata_with_zone_boundaries(session: dict[str, Any]) -> dict[str, Any]:
    row = _ORIGINAL_METADATA(session)
    boundaries = session.get("zone_cycle_boundaries") or []
    row["zone_cycle_boundaries"] = [
        deepcopy(item) for item in boundaries if isinstance(item, dict)
    ]
    width = coordinator_module._as_float(session.get("mowing_path_width_m"))
    if width is not None:
        row["mowing_path_width_m"] = width
    return row


def _session_gap_mergeable(previous: dict[str, Any], continuation: dict[str, Any]) -> bool:
    previous_end = history_module._session_end_ms(previous)
    continuation_start = history_module._as_int(continuation.get("started_at_ms"))
    if previous_end is None or continuation_start is None:
        return False
    gap_ms = continuation_start - previous_end
    return (
        -history_module._SESSION_CLOCK_SKEW_MS
        <= gap_ms
        <= history_module._SESSION_MERGE_GAP_MS
    )


def _sessions_can_merge_with_partial_repair(
    previous: dict[str, Any],
    continuation: dict[str, Any],
) -> bool:
    """Treat old beta1 partial progress resets as session fragments, not cycles."""
    if previous.get("legacy") or continuation.get("legacy"):
        return False
    if str(previous.get("completion_reason") or "") == "vendor_cycle_reset_partial":
        return _session_gap_mergeable(previous, continuation)
    return _ORIGINAL_SESSIONS_CAN_MERGE(previous, continuation)


def _boundary_rows_from_partial_session(session: dict[str, Any]) -> list[dict[str, Any]]:
    if str(session.get("completion_reason") or "") != "vendor_cycle_reset_partial":
        return []
    at_ms = history_module._session_end_ms(session)
    if at_ms is None:
        return []
    zone_ids = history_module._unique_ints(
        session.get("cycle_reset_zone_ids"),
        (session.get("final_progress") or {}).keys(),
    )
    return [
        {
            "zone_id": zone_id,
            "at_ms": at_ms,
            "at": history_module._iso(at_ms),
            "previous_progress": history_module._as_int(
                (session.get("final_progress") or {}).get(str(zone_id))
            ),
            "source": "migrated_beta1_partial_reset",
        }
        for zone_id in zone_ids
    ]


def _merge_session_records_with_boundaries(
    previous: dict[str, Any],
    continuation: dict[str, Any],
) -> dict[str, Any]:
    previous_partial = (
        str(previous.get("completion_reason") or "") == "vendor_cycle_reset_partial"
    )
    continuation_partial = (
        str(continuation.get("completion_reason") or "")
        == "vendor_cycle_reset_partial"
    )
    merged = _ORIGINAL_MERGE_SESSION_RECORDS(previous, continuation)

    boundaries = [
        *(
            deepcopy(previous.get("zone_cycle_boundaries"))
            if isinstance(previous.get("zone_cycle_boundaries"), list)
            else []
        ),
        *_boundary_rows_from_partial_session(previous),
        *(
            deepcopy(continuation.get("zone_cycle_boundaries"))
            if isinstance(continuation.get("zone_cycle_boundaries"), list)
            else []
        ),
    ]
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[int | None, int | None, str]] = set()
    for item in boundaries:
        if not isinstance(item, dict):
            continue
        key = (
            history_module._as_int(item.get("zone_id")),
            history_module._as_int(item.get("at_ms")),
            str(item.get("source") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(dict(item))
    merged["zone_cycle_boundaries"] = deduplicated

    # Old beta1 partial resets were implementation artefacts.  Their reset IDs
    # are now represented as per-zone boundary timestamps and must not keep
    # acting as whole-session reset markers in daily-trail reconstruction.
    if previous_partial or continuation_partial:
        merged["cycle_reset_zone_ids"] = []
        if not merged.get("active") and str(merged.get("completion_reason") or "") == "vendor_cycle_reset_partial":
            merged["completion_reason"] = None
            merged["completed"] = None
            merged["final_progress"] = {}
    return merged


def _vendor_progress_by_zone(snapshot: dict[str, Any]) -> dict[int, int]:
    result: dict[int, int] = {}
    for item in (snapshot.get("coverage") or {}).get("zones") or []:
        if not isinstance(item, dict):
            continue
        zone_id = history_module._as_int(item.get("id"))
        progress = history_module._as_int(item.get("pct"))
        if zone_id is not None and progress is not None and 0 <= progress <= 100:
            result[zone_id] = progress
    for item in snapshot.get("zone_details") or []:
        if not isinstance(item, dict):
            continue
        zone_id = history_module._as_int(item.get("id"))
        if zone_id is None:
            continue
        progress = history_module._as_int(
            item.get("vendor_percentage")
            if item.get("vendor_percentage") is not None
            else item.get("percentage")
        )
        if progress is not None and 0 <= progress <= 100:
            result[zone_id] = progress
    return result


def _update_from_snapshot_with_progress_owner(
    self: history_module.NavimowerHistory,
    snapshot: dict[str, Any],
) -> None:
    """Heal task progress after the legacy physical-zone ownership update."""
    _ORIGINAL_UPDATE_FROM_SNAPSHOT(self, snapshot)
    owner = history_module._as_int(snapshot.get("active_zone_progress_zone_id"))
    owner_progress = history_module._as_int(snapshot.get("active_zone_progress"))
    vendor = _vendor_progress_by_zone(snapshot)
    swath = coordinator_module._as_float(snapshot.get("mowing_path_width_m"))

    changed = False
    with self._lock:
        active = self._cache.get(self._active_id or "")
        if active is None:
            return
        task_progress = active.setdefault("task_zone_progress", {})
        selected = history_module._unique_ints(active.get("zone_ids"))

        # Reconstruct non-active zones from the vendor's own per-zone counters.
        # This removes beta1 values that were accidentally written to whichever
        # physical polygon the mower happened to occupy at the time.
        for zone_id in selected:
            value = vendor.get(zone_id)
            if value is not None and history_module._as_int(task_progress.get(str(zone_id))) != value:
                task_progress[str(zone_id)] = value
                changed = True

        if (
            owner is not None
            and owner in selected
            and owner_progress is not None
            and 0 <= owner_progress <= 100
            and history_module._as_int(task_progress.get(str(owner))) != owner_progress
        ):
            task_progress[str(owner)] = owner_progress
            changed = True

        if swath is not None and 0.10 <= swath <= 2.0:
            if coordinator_module._as_float(active.get("mowing_path_width_m")) != swath:
                active["mowing_path_width_m"] = swath
                changed = True

        percentages = [
            history_module._as_int(task_progress.get(str(zone_id)))
            for zone_id in selected
        ]
        known = [value for value in percentages if value is not None]
        completed = bool(selected) and len(known) == len(selected) and all(
            value >= VENDOR_COMPLETION_PROGRESS_MIN for value in known
        )
        if completed:
            if active.get("completed") is not True:
                active["completed"] = True
                active["completion_reason"] = active.get("completion_reason") or "vendor_progress"
                changed = True
        elif active.get("completion_reason") == "vendor_progress":
            active["completed"] = None
            active["completion_reason"] = None
            active["final_progress"] = {}
            changed = True

        if changed:
            self._update_active_metadata_locked(active)

    if changed:
        self._schedule_index_save()
        self._schedule_active_save()


def _confirmed_zone_cycle_reset(
    *,
    owner_zone_id: int | None,
    zone_id: int,
    peak_progress: int | None,
    new_progress: int | None,
    old_start: int | None,
    new_start: int | None,
) -> bool:
    """Return whether telemetry proves a new cycle in the *same* work zone."""
    if owner_zone_id is None or owner_zone_id != zone_id:
        return False
    if (
        peak_progress is None
        or new_progress is None
        or old_start is None
        or new_start is None
    ):
        return False
    return bool(
        peak_progress >= VENDOR_COMPLETION_PROGRESS_MIN
        and new_start > old_start
        and new_progress <= 25
        and peak_progress - new_progress >= 30
    )


def _prepare_cycle_without_session_split(
    self: history_module.NavimowerHistory,
    snapshot: dict[str, Any],
    *,
    pose_time: Any,
) -> bool:
    """Record confirmed per-zone boundaries without closing the mowing session."""
    rows = self._progress_rows(snapshot)
    if not rows:
        return False
    owner_zone_id = history_module._as_int(snapshot.get("active_zone_progress_zone_id"))
    reset_rows: list[tuple[dict[str, Any], dict[str, Any], int]] = []

    with self._lock:
        active = self._cache.get(self._active_id or "")
        for row in rows:
            zone_id = history_module._as_int(row.get("id"))
            if zone_id is None:
                continue
            previous = dict(self._zone_progress_state.get(str(zone_id)) or {})
            old_progress = history_module._as_int(previous.get("progress"))
            peak_progress = history_module._as_int(previous.get("peak_progress"))
            if peak_progress is None:
                peak_progress = old_progress
            new_progress = history_module._as_int(row.get("progress"))
            old_start = history_module._as_int(previous.get("start_time"))
            new_start = history_module._as_int(row.get("start_time"))
            if active is not None and _confirmed_zone_cycle_reset(
                owner_zone_id=owner_zone_id,
                zone_id=zone_id,
                peak_progress=peak_progress,
                new_progress=new_progress,
                old_start=old_start,
                new_start=new_start,
            ):
                reset_rows.append((previous, row, peak_progress or 0))

        reset_ids: set[int] = set()
        if reset_rows and active is not None:
            boundaries = active.setdefault("zone_cycle_boundaries", [])
            task_progress = active.setdefault("task_zone_progress", {})
            event_progress: dict[str, int] = {}
            event_ids: list[int] = []
            for _previous, row, previous_peak in reset_rows:
                zone_id = history_module._as_int(row.get("id"))
                if zone_id is None:
                    continue
                reset_ids.add(zone_id)
                event_ids.append(zone_id)
                event_progress[str(zone_id)] = previous_peak
                new_start = history_module._as_int(row.get("start_time"))
                boundary_ms = (
                    history_module._timestamp_ms(new_start)
                    if new_start is not None
                    else history_module._timestamp_ms(pose_time)
                )
                boundary = {
                    "zone_id": zone_id,
                    "at_ms": boundary_ms,
                    "at": history_module._iso(boundary_ms),
                    "previous_progress": previous_peak,
                    "source": "vendor_zone_cycle_reset",
                }
                if not any(
                    history_module._as_int(item.get("zone_id")) == zone_id
                    and history_module._as_int(item.get("at_ms")) == boundary_ms
                    for item in boundaries
                    if isinstance(item, dict)
                ):
                    boundaries.append(boundary)
                new_progress = history_module._as_int(row.get("progress"))
                if new_progress is not None:
                    task_progress[str(zone_id)] = new_progress

                record = dict(self._zone_history.get(str(zone_id)) or {})
                record.update(
                    {
                        "id": zone_id,
                        "name": row.get("name") or record.get("name") or f"Zone {zone_id}",
                        "last_completed_at": history_module._iso(boundary_ms),
                        "last_completed_progress": previous_peak,
                    }
                )
                self._zone_history[str(zone_id)] = record

            self._last_cycle_event = {
                "reason": "vendor_zone_cycle_reset",
                "at_ms": history_module._timestamp_ms(pose_time),
                "at": history_module._iso(history_module._timestamp_ms(pose_time)),
                "zone_ids": event_ids,
                "completed": True,
                "final_progress": event_progress,
                "source": "vendor_progress",
            }
            self._update_active_metadata_locked(active)

        for row in rows:
            zone_id = history_module._as_int(row.get("id"))
            if zone_id is None:
                continue
            progress = history_module._as_int(row.get("progress"))
            previous = self._zone_progress_state.get(str(zone_id)) or {}
            previous_peak = history_module._as_int(previous.get("peak_progress"))
            if previous_peak is None:
                previous_peak = history_module._as_int(previous.get("progress"))
            if zone_id in reset_ids:
                peak = progress
            else:
                known = [value for value in (previous_peak, progress) if value is not None]
                peak = max(known) if known else None
            self._zone_progress_state[str(zone_id)] = {
                "progress": progress,
                "peak_progress": peak,
                "start_time": history_module._as_int(row.get("start_time")),
                "end_time": history_module._as_int(row.get("end_time")),
                "observed_at_ms": history_module._timestamp_ms(pose_time),
            }

    self._schedule_index_save()
    if reset_rows:
        self._schedule_active_save()
    return bool(reset_rows)


def apply_runtime_compat_fixes() -> None:
    """Install the normalization hooks once for this Home Assistant process."""
    global _INSTALLED
    if _INSTALLED:
        return
    coordinator_module.NavimowCoordinator._parse = _parse_with_compat
    history_module._metadata = _metadata_with_zone_boundaries
    history_module._sessions_can_merge = _sessions_can_merge_with_partial_repair
    history_module._merge_session_records = _merge_session_records_with_boundaries
    history_module.NavimowerHistory.update_from_snapshot = _update_from_snapshot_with_progress_owner
    history_module.NavimowerHistory.prepare_cycle = _prepare_cycle_without_session_split
    _INSTALLED = True
