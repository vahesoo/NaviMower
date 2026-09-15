"""Canonical persistent per-zone state reducer for Navimower.

The vendor ``get-path-info-time`` rows are the authority for zone coverage and
finished area. MQTT work/route counters remain live task context only; they do
not compete with zone coverage. The reducer is intentionally pure so cycle,
completion and sticky-state semantics can be table-tested without Home
Assistant or coordinator monkeypatches.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
from typing import Any

LEDGER_VERSION = 1
COMPLETION_THRESHOLD = 100
COVERAGE_FRESH_MAX_AGE_S = 90.0
RESET_CONFIRMATIONS_REQUIRED = 2
RESET_CANDIDATE_MAX_AGE_MS = 60_000
RESET_LOW_PROGRESS_MAX = 25
RESET_LIVE_CONTRADICTION_MIN = 30


def as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def clamp_pct(value: Any) -> float | None:
    parsed = as_float(value)
    if parsed is None or parsed < 0 or parsed > 100:
        return None
    return parsed


def timestamp_ms(value: Any = None) -> int:
    parsed = as_int(value)
    if parsed is None or parsed <= 0:
        return int(datetime.now(UTC).timestamp() * 1000)
    return parsed * 1000 if parsed < 10_000_000_000 else parsed


def iso_from_ms(value: Any) -> str | None:
    parsed = as_int(value)
    if parsed is None or parsed <= 0:
        return None
    try:
        return datetime.fromtimestamp(parsed / 1000.0, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def iso_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1000)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _latest_iso(*values: Any) -> str | None:
    known = [str(value) for value in values if value]
    return max(known) if known else None


def _canonical_polygon(polygon: Any) -> tuple[tuple[float, float], ...] | None:
    if not isinstance(polygon, list):
        return None
    points: list[tuple[float, float]] = []
    for raw in polygon:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            return None
        x, y = as_float(raw[0]), as_float(raw[1])
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


def zone_geometry_signature(zone: dict[str, Any] | None) -> str | None:
    row = zone or {}
    area = as_float(row.get("area") if row.get("area") is not None else row.get("area_m2"))
    polygon = _canonical_polygon(row.get("polygon"))
    if area is None or area < 0 or polygon is None:
        return None
    payload = "{:.2f}|{}".format(
        round(area, 2),
        ";".join(f"{x:.3f},{y:.3f}" for x, y in polygon),
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def new_ledger_state() -> dict[str, Any]:
    return {
        "version": LEDGER_VERSION,
        "revision": 0,
        "zones": {},
        "reset_candidates": {},
        "last_event": None,
    }


def normalize_ledger_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or as_int(value.get("version")) != LEDGER_VERSION:
        return new_ledger_state()
    state = new_ledger_state()
    state["revision"] = max(0, as_int(value.get("revision")) or 0)
    zones = value.get("zones")
    if isinstance(zones, dict):
        state["zones"] = {
            str(key): dict(row)
            for key, row in zones.items()
            if isinstance(row, dict)
        }
    candidates = value.get("reset_candidates")
    if isinstance(candidates, dict):
        state["reset_candidates"] = {
            str(key): dict(row)
            for key, row in candidates.items()
            if isinstance(row, dict)
        }
    if isinstance(value.get("last_event"), dict):
        state["last_event"] = dict(value["last_event"])
    return state


def _unique_zone_ids(values: Any) -> list[int]:
    result: list[int] = []
    for raw in values or []:
        zone_id = as_int(raw)
        if zone_id is not None and zone_id > 0 and zone_id not in result:
            result.append(zone_id)
    return result


def _task_zone_ids(
    snapshot: dict[str, Any],
    active_session: dict[str, Any] | None,
    all_zone_ids: list[int],
) -> list[int]:
    session_ids = _unique_zone_ids((active_session or {}).get("zone_ids"))
    target_ids = _unique_zone_ids(snapshot.get("target_zone_ids"))
    current_ids = _unique_zone_ids(snapshot.get("current_zone_ids"))
    selected = session_ids or target_ids or current_ids
    activity = str(snapshot.get("activity") or "").lower()
    if not selected and activity in {"mowing", "paused", "returning"}:
        if str(snapshot.get("current_zone") or "").lower() == "all":
            selected = list(all_zone_ids)
    return selected


def _hard_reset_drop(old_peak: float | None, new_progress: float | None) -> bool:
    if old_peak is None or new_progress is None:
        return False
    drop = old_peak - new_progress
    return bool(
        (old_peak >= 15 and new_progress <= 5 and drop >= 15)
        or (old_peak >= 50 and new_progress <= 20 and drop >= 30)
    )


def _vendor_cycle_key(zone_id: int, start_time: int | None, sequence: int) -> str:
    if start_time is not None and start_time > 0:
        return f"vendor:{zone_id}:{start_time}"
    return f"local:{zone_id}:{sequence}"


def _semantic_signature(zones: dict[str, dict[str, Any]]) -> tuple[Any, ...]:
    return tuple(
        (
            key,
            row.get("geometry_signature"),
            row.get("cycle_key"),
            row.get("vendor_start_time"),
            row.get("vendor_end_time"),
            row.get("progress_pct"),
            row.get("progress_peak_pct"),
            row.get("mowed_area_m2"),
            row.get("last_started_at"),
            row.get("last_mowed_at"),
            row.get("last_completed_at"),
            row.get("last_completed_cycle_key"),
            row.get("pending_vendor_cycle"),
        )
        for key, row in sorted(zones.items(), key=lambda item: as_int(item[0]) or 0)
    )


def mark_explicit_reset(
    state_value: Any,
    zone_ids: list[int],
    *,
    observed_at_ms: int,
    reason: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reset only the explicitly selected zones while waiting for vendor ack."""
    state = normalize_ledger_state(state_value)
    before = _semantic_signature(state["zones"])
    events: list[dict[str, Any]] = []
    for zone_id in _unique_zone_ids(zone_ids):
        key = str(zone_id)
        row = dict(state["zones"].get(key) or {"id": zone_id})
        sequence = (as_int(row.get("cycle_sequence")) or 0) + 1
        previous_start = as_int(row.get("vendor_start_time"))
        row.update(
            {
                "id": zone_id,
                "cycle_sequence": sequence,
                "cycle_key": f"local-reset:{zone_id}:{observed_at_ms}:{sequence}",
                "progress_pct": 0.0,
                "progress_peak_pct": 0.0,
                "mowed_area_m2": 0.0,
                "completed_current_cycle": False,
                "pending_vendor_cycle": True,
                "pending_reset_at_ms": observed_at_ms,
                "pending_reset_reason": str(reason),
                "previous_vendor_start_time": previous_start,
                "progress_source": "explicit_reset_pending_vendor",
                "progress_guard": True,
                "progress_guard_reason": "awaiting_vendor_cycle",
                "stale": False,
                "updated_at": iso_from_ms(observed_at_ms),
            }
        )
        state["zones"][key] = row
        state["reset_candidates"].pop(key, None)
        event = {
            "type": "zone_cycle_reset",
            "zone_id": zone_id,
            "reason": str(reason),
            "at_ms": observed_at_ms,
            "cycle_key": row["cycle_key"],
            "source": "explicit_command",
        }
        events.append(event)
        state["last_event"] = event
    if _semantic_signature(state["zones"]) != before:
        state["revision"] = (as_int(state.get("revision")) or 0) + 1
    return state, events


def _history_seed(record: dict[str, Any], history: dict[str, Any]) -> None:
    record["last_started_at"] = _latest_iso(
        record.get("last_started_at"), history.get("last_started_at")
    )
    record["last_mowed_at"] = _latest_iso(
        record.get("last_mowed_at"), history.get("last_mowed_at")
    )
    # History remains a migration/backfill source for timestamp facts. The
    # ledger never lets a later stale value move completion backwards.
    previous_completed = iso_ms(record.get("last_completed_at"))
    history_completed = iso_ms(history.get("last_completed_at"))
    if history_completed is not None and (
        previous_completed is None or history_completed > previous_completed
    ):
        record["last_completed_at"] = history.get("last_completed_at")
        record["last_completed_progress"] = history.get("last_completed_progress")
        record["last_completed_source"] = history.get("last_completed_source")
        record["last_completed_confirmation"] = history.get(
            "last_completed_confirmation"
        )
        record["last_completed_cycle_key"] = history.get("last_completed_cycle_id")


def _completion_time_ms(
    vendor_end: int | None,
    vendor_start: int | None,
    observed_at_ms: int,
    previous_completed_at: Any,
) -> int:
    previous_ms = iso_ms(previous_completed_at)
    end_ms = timestamp_ms(vendor_end) if vendor_end else None
    start_ms = timestamp_ms(vendor_start) if vendor_start else None
    if (
        end_ms is not None
        and (start_ms is None or end_ms >= start_ms)
        and end_ms <= observed_at_ms + 120_000
        and (previous_ms is None or end_ms > previous_ms)
    ):
        return end_ms
    return observed_at_ms


def reduce_zone_ledger(
    state_value: Any,
    *,
    snapshot: dict[str, Any],
    map_zones: list[dict[str, Any]],
    zone_details: list[dict[str, Any]],
    zone_history: dict[str, dict[str, Any]],
    active_session: dict[str, Any] | None,
    observed_at_ms: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Reduce one snapshot into canonical zones, totals, task and cycle events."""
    state = normalize_ledger_state(state_value)
    before = _semantic_signature(state["zones"])
    previous_zones = deepcopy(state["zones"])
    candidates = state["reset_candidates"]
    events: list[dict[str, Any]] = []

    map_by_id = {
        zone_id: dict(item)
        for item in map_zones or []
        if isinstance(item, dict)
        and (zone_id := as_int(item.get("id"))) is not None
        and zone_id > 0
    }
    detail_by_id = {
        zone_id: dict(item)
        for item in zone_details or []
        if isinstance(item, dict)
        and (zone_id := as_int(item.get("id"))) is not None
        and zone_id > 0
    }
    coverage = snapshot.get("coverage")
    coverage_by_id = {
        zone_id: dict(item)
        for item in (coverage.get("zones") if isinstance(coverage, dict) else []) or []
        if isinstance(item, dict)
        and (zone_id := as_int(item.get("id"))) is not None
        and zone_id > 0
    }
    history_by_id = {
        zone_id: dict(item)
        for key, item in (zone_history or {}).items()
        if isinstance(item, dict)
        and (zone_id := as_int(item.get("id")) or as_int(key)) is not None
        and zone_id > 0
    }
    previous_ids = {
        zone_id
        for key in previous_zones
        if (zone_id := as_int(key)) is not None and zone_id > 0
    }
    all_zone_ids = sorted(set(map_by_id) | set(detail_by_id) | set(coverage_by_id) | previous_ids)

    coverage_age = as_float(snapshot.get("coverage_source_age"))
    coverage_fresh = bool(
        coverage_by_id
        and (
            coverage_age is None
            or 0 <= coverage_age <= COVERAGE_FRESH_MAX_AGE_S
        )
    )
    active_zone_id = as_int(snapshot.get("active_zone_progress_zone_id"))
    if active_zone_id is None:
        active_zone_id = as_int(snapshot.get("current_physical_zone_id"))
    live_work_progress = clamp_pct(snapshot.get("active_zone_progress"))

    resolved_zones: dict[str, dict[str, Any]] = {}
    for zone_id in all_zone_ids:
        key = str(zone_id)
        previous = dict(previous_zones.get(key) or {})
        map_row = map_by_id.get(zone_id) or {}
        detail = detail_by_id.get(zone_id) or {}
        vendor = coverage_by_id.get(zone_id) or {}
        history = history_by_id.get(zone_id) or {}

        area = next(
            (
                value
                for value in (
                    as_float(map_row.get("area")),
                    as_float(detail.get("area_m2")),
                    as_float(vendor.get("area")),
                    as_float(previous.get("area_m2")),
                    as_float(history.get("area_m2")),
                )
                if value is not None and value >= 0
            ),
            None,
        )
        geometry_signature = zone_geometry_signature(map_row) or previous.get(
            "geometry_signature"
        )
        record = dict(previous)
        record.update(
            {
                "id": zone_id,
                "name": str(
                    detail.get("name")
                    or map_row.get("name")
                    or vendor.get("name")
                    or previous.get("name")
                    or history.get("name")
                    or f"Zone {zone_id}"
                ),
                "area_m2": round(area, 2) if area is not None else None,
                "geometry_signature": geometry_signature,
                "cutting_height_mm": detail.get("cutting_height_mm"),
            }
        )
        _history_seed(record, history)

        row_fresh = coverage_fresh and bool(vendor)
        raw_pct = clamp_pct(vendor.get("pct")) if row_fresh else None
        raw_finished = as_float(vendor.get("finished")) if row_fresh else None
        vendor_start = as_int(vendor.get("start_time")) if row_fresh else None
        vendor_end = as_int(vendor.get("end_time")) if row_fresh else None
        if row_fresh:
            record["vendor_coverage_pct"] = (
                round(raw_pct, 1) if raw_pct is not None else None
            )
            record["vendor_finished_area_m2"] = (
                round(raw_finished, 2)
                if raw_finished is not None and raw_finished >= 0
                else None
            )
            record["source_age_s"] = round(coverage_age, 3) if coverage_age is not None else 0.0
        elif previous:
            record["source_age_s"] = coverage_age

        previous_progress = clamp_pct(previous.get("progress_pct"))
        previous_peak = clamp_pct(previous.get("progress_peak_pct"))
        if previous_peak is None:
            previous_peak = previous_progress
        previous_start = as_int(previous.get("vendor_start_time"))
        previous_geometry = previous.get("geometry_signature")
        sequence = as_int(previous.get("cycle_sequence")) or 0
        pending_vendor_cycle = bool(previous.get("pending_vendor_cycle"))
        confirmed_reset = False
        reset_reason: str | None = None

        if row_fresh and raw_pct is not None:
            if pending_vendor_cycle:
                prior_vendor_start = as_int(previous.get("previous_vendor_start_time"))
                if prior_vendor_start is None:
                    prior_vendor_start = previous_start
                vendor_advanced = bool(
                    vendor_start is not None
                    and (prior_vendor_start is None or vendor_start > prior_vendor_start)
                )
                vendor_low_ack = raw_pct <= RESET_LOW_PROGRESS_MAX
                if vendor_advanced or vendor_low_ack:
                    confirmed_reset = True
                    reset_reason = (
                        "explicit_reset_vendor_start"
                        if vendor_advanced
                        else "explicit_reset_vendor_low"
                    )
                    pending_vendor_cycle = False
                else:
                    # The command has reset the public cycle already. Do not let
                    # an old cached 100% row resurrect it while the vendor catches up.
                    record.update(
                        {
                            "progress_pct": 0.0,
                            "progress_peak_pct": 0.0,
                            "mowed_area_m2": 0.0,
                            "pending_vendor_cycle": True,
                            "progress_source": "explicit_reset_pending_vendor",
                            "progress_guard": True,
                            "progress_guard_reason": "awaiting_vendor_cycle",
                            "stale": False,
                            "updated_at": iso_from_ms(observed_at_ms),
                        }
                    )
                    resolved_zones[key] = record
                    continue
            elif (
                previous
                and vendor_start is not None
                and previous_start is not None
                and vendor_start > previous_start
            ):
                confirmed_reset = True
                reset_reason = "vendor_start_time"
            elif (
                previous
                and geometry_signature is not None
                and previous_geometry is not None
                and geometry_signature != previous_geometry
                and previous_progress is not None
                and raw_pct < previous_progress
            ):
                confirmed_reset = True
                reset_reason = "zone_geometry_changed"
            elif previous and _hard_reset_drop(previous_peak, raw_pct):
                live_contradicts = bool(
                    active_zone_id == zone_id
                    and live_work_progress is not None
                    and live_work_progress > RESET_LIVE_CONTRADICTION_MIN
                )
                candidate = dict(candidates.get(key) or {})
                first_ms = as_int(candidate.get("first_ms"))
                recent = bool(
                    first_ms is not None
                    and 0 <= observed_at_ms - first_ms <= RESET_CANDIDATE_MAX_AGE_MS
                )
                same_start = candidate.get("start_time") == vendor_start
                count = as_int(candidate.get("count")) or 0
                if live_contradicts:
                    count = 0
                elif recent and same_start:
                    count += 1
                else:
                    count = 1
                    first_ms = observed_at_ms
                candidates[key] = {
                    "first_ms": first_ms,
                    "last_ms": observed_at_ms,
                    "count": count,
                    "start_time": vendor_start,
                    "progress": raw_pct,
                    "previous_peak": previous_peak,
                    "live_progress": live_work_progress,
                    "live_contradicts": live_contradicts,
                }
                if count >= RESET_CONFIRMATIONS_REQUIRED and not live_contradicts:
                    confirmed_reset = True
                    reset_reason = "confirmed_vendor_low_drop"
                    candidates.pop(key, None)
            else:
                candidates.pop(key, None)

            if confirmed_reset:
                sequence += 1
                cycle_key = _vendor_cycle_key(zone_id, vendor_start, sequence)
                accepted = raw_pct
                peak = raw_pct
                event = {
                    "type": "zone_cycle_reset",
                    "zone_id": zone_id,
                    "reason": reset_reason,
                    "at_ms": observed_at_ms,
                    "cycle_key": cycle_key,
                    "source": "vendor_coverage",
                }
                events.append(event)
                state["last_event"] = event
            else:
                cycle_key = str(previous.get("cycle_key") or "") or _vendor_cycle_key(
                    zone_id, vendor_start, max(1, sequence)
                )
                if str(cycle_key).startswith("local-reset:") and vendor_start is not None:
                    cycle_key = _vendor_cycle_key(zone_id, vendor_start, max(1, sequence))
                accepted = raw_pct if previous_progress is None else max(previous_progress, raw_pct)
                peak = accepted if previous_peak is None else max(previous_peak, accepted)

            held = bool(raw_pct < accepted)
            if held:
                mowed_area = area * accepted / 100.0 if area is not None else previous.get("mowed_area_m2")
                progress_source = "vendor_coverage_monotonic_hold"
            elif raw_finished is not None and raw_finished >= 0:
                mowed_area = raw_finished
                progress_source = "vendor_coverage"
            else:
                mowed_area = area * accepted / 100.0 if area is not None else None
                progress_source = "vendor_coverage_calculated_area"
            if area is not None and mowed_area is not None:
                mowed_area = max(0.0, min(area, float(mowed_area)))

            previous_same_cycle_progress = (
                0.0 if confirmed_reset else previous_progress
            )
            transitioned_to_100 = bool(
                raw_pct >= COMPLETION_THRESHOLD
                and (
                    confirmed_reset
                    or previous_same_cycle_progress is not None
                    and previous_same_cycle_progress < COMPLETION_THRESHOLD
                )
            )
            if transitioned_to_100:
                completed_ms = _completion_time_ms(
                    vendor_end,
                    vendor_start,
                    observed_at_ms,
                    record.get("last_completed_at"),
                )
                record.update(
                    {
                        "last_completed_at": iso_from_ms(completed_ms),
                        "last_completed_progress": 100,
                        "last_completed_source": "private_zone_coverage",
                        "last_completed_confirmation": "coverage_100_transition",
                        "last_completed_cycle_key": cycle_key,
                    }
                )
                event = {
                    "type": "zone_completed",
                    "zone_id": zone_id,
                    "at_ms": completed_ms,
                    "cycle_key": cycle_key,
                    "source": "vendor_coverage",
                }
                events.append(event)
                state["last_event"] = event

            record.update(
                {
                    "cycle_sequence": max(1, sequence),
                    "cycle_key": cycle_key,
                    "vendor_start_time": vendor_start,
                    "vendor_end_time": vendor_end,
                    "progress_pct": round(accepted, 1),
                    "progress_peak_pct": round(peak, 1),
                    "mowed_area_m2": round(float(mowed_area), 2) if mowed_area is not None else None,
                    "completed_current_cycle": bool(accepted >= COMPLETION_THRESHOLD),
                    "pending_vendor_cycle": pending_vendor_cycle,
                    "progress_source": progress_source,
                    "progress_guard": held,
                    "progress_guard_reason": "same_cycle_vendor_regression" if held else None,
                    "stale": False,
                    "updated_at": iso_from_ms(observed_at_ms),
                }
            )
            if not pending_vendor_cycle:
                record.pop("pending_reset_at_ms", None)
                record.pop("pending_reset_reason", None)
                record.pop("previous_vendor_start_time", None)
        else:
            # No fresh row: last-good zone state is authoritative. A first-run
            # migration may seed only the known vendor percentage from History.
            if not previous:
                seeded = clamp_pct(
                    history.get("vendor_percentage")
                    if history.get("vendor_percentage") is not None
                    else history.get("percentage")
                )
                if seeded is not None:
                    sequence = 1
                    record["cycle_sequence"] = sequence
                    record["cycle_key"] = _vendor_cycle_key(zone_id, None, sequence)
                    record["progress_pct"] = seeded
                    record["progress_peak_pct"] = seeded
                    record["mowed_area_m2"] = (
                        round(area * seeded / 100.0, 2) if area is not None else None
                    )
                    record["progress_source"] = "persisted_vendor_history_seed"
            record["stale"] = True
            record["updated_at"] = record.get("updated_at") or iso_from_ms(observed_at_ms)

        resolved_zones[key] = record

    state["zones"] = resolved_zones
    if _semantic_signature(resolved_zones) != before:
        state["revision"] = (as_int(state.get("revision")) or 0) + 1

    task_zone_ids = _task_zone_ids(snapshot, active_session, all_zone_ids)
    task_zone_set = set(task_zone_ids)
    visited_zone_ids = set(_unique_zone_ids((active_session or {}).get("visited_zone_ids")))
    public_rows: list[dict[str, Any]] = []
    for zone_id in all_zone_ids:
        record = resolved_zones.get(str(zone_id)) or {}
        progress = clamp_pct(record.get("progress_pct"))
        public_rows.append(
            {
                "id": zone_id,
                "name": record.get("name") or f"Zone {zone_id}",
                "area_m2": record.get("area_m2"),
                "coverage_pct": round(progress, 1) if progress is not None else None,
                "mowed_area_m2": record.get("mowed_area_m2"),
                "vendor_coverage_pct": record.get("vendor_coverage_pct"),
                "vendor_finished_area_m2": record.get("vendor_finished_area_m2"),
                "vendor_start_time": record.get("vendor_start_time"),
                "vendor_end_time": record.get("vendor_end_time"),
                "task_progress_pct": round(progress, 1) if progress is not None and zone_id in task_zone_set else None,
                "active": zone_id == active_zone_id,
                "selected_in_task": zone_id in task_zone_set,
                "visited_in_task": zone_id in visited_zone_ids,
                "cycle_id": record.get("cycle_key"),
                "last_started_at": record.get("last_started_at"),
                "last_mowed_at": record.get("last_mowed_at"),
                "last_completed_at": record.get("last_completed_at"),
                "last_completed_progress": record.get("last_completed_progress"),
                "last_completed_source": record.get("last_completed_source"),
                "last_completed_confirmation": record.get("last_completed_confirmation"),
                "last_completed_cycle_id": record.get("last_completed_cycle_key"),
                "progress_source": record.get("progress_source"),
                "progress_guard": bool(record.get("progress_guard")),
                "progress_guard_reason": record.get("progress_guard_reason"),
                "pending_vendor_cycle": bool(record.get("pending_vendor_cycle")),
                "cutting_height_mm": record.get("cutting_height_mm"),
                "stale": bool(record.get("stale")),
                "source_age_s": record.get("source_age_s"),
            }
        )

    known_area_rows = [row for row in public_rows if as_float(row.get("area_m2")) is not None]
    map_area = sum(as_float(row.get("area_m2")) or 0.0 for row in known_area_rows)
    map_mowed = sum(as_float(row.get("mowed_area_m2")) or 0.0 for row in known_area_rows)
    map_coverage = 100.0 * map_mowed / map_area if map_area > 0 else None

    task_rows = [row for row in public_rows if row.get("selected_in_task")]
    task_area = sum(as_float(row.get("area_m2")) or 0.0 for row in task_rows)
    weighted_task_mowed = sum(
        (as_float(row.get("area_m2")) or 0.0)
        * (as_float(row.get("coverage_pct")) or 0.0)
        / 100.0
        for row in task_rows
    )
    weighted_task_pct = (
        100.0 * weighted_task_mowed / task_area if task_area > 0 else None
    )
    direct_task_pct = clamp_pct(snapshot.get("mowing_progress"))
    direct_task_mowed = as_float(snapshot.get("session_area"))
    if direct_task_mowed is not None and direct_task_mowed < 0:
        direct_task_mowed = None
    if task_area > 0 and direct_task_mowed is not None:
        direct_task_mowed = min(task_area, direct_task_mowed)

    if direct_task_pct is not None:
        task_pct = direct_task_pct
        task_progress_source = snapshot.get("mowing_progress_source") or "vendor_overall"
    else:
        task_pct = weighted_task_pct
        task_progress_source = "area_weighted_zone_coverage" if weighted_task_pct is not None else None
    if direct_task_mowed is not None:
        task_mowed = direct_task_mowed
        task_area_source = snapshot.get("session_area_source") or "vendor_subtotal"
    elif task_area > 0 and task_pct is not None:
        task_mowed = task_area * task_pct / 100.0
        task_area_source = "task_progress_calculated"
    else:
        task_mowed = weighted_task_mowed if task_area > 0 else None
        task_area_source = "area_weighted_zone_coverage" if task_area > 0 else None

    task = {
        "progress_pct": round(task_pct, 1) if task_pct is not None else None,
        "mowed_area_m2": round(task_mowed, 2) if task_mowed is not None else None,
        "area_m2": round(task_area, 2) if task_area > 0 else None,
        "zone_ids": list(task_zone_ids),
        "active_zone_id": active_zone_id,
        "progress_source": task_progress_source,
        "progress_source_age_s": snapshot.get("mowing_progress_source_age"),
        "mowed_area_source": task_area_source,
        "mowed_area_source_age_s": snapshot.get("session_area_source_age"),
        "zone_weighted_progress_pct": round(weighted_task_pct, 1) if weighted_task_pct is not None else None,
    }

    completed_values = [row.get("last_completed_at") for row in public_rows]
    totals = {
        "map_area_m2": round(map_area, 2) if map_area > 0 else None,
        "map_mowed_area_m2": round(map_mowed, 2) if map_area > 0 else None,
        "map_coverage_pct": round(map_coverage, 1) if map_coverage is not None else None,
        "task_area_m2": task["area_m2"],
        "task_mowed_area_m2": task["mowed_area_m2"],
        "task_progress_pct": task["progress_pct"],
        "task_progress_source": task["progress_source"],
        "task_mowed_area_source": task["mowed_area_source"],
        "task_zone_progress_weighted_pct": task["zone_weighted_progress_pct"],
        "task_zone_ids": list(task_zone_ids),
        "active_zone_id": active_zone_id,
        "zone_count": len(public_rows),
        "completed_zone_count": sum(
            1 for row in public_rows if (as_float(row.get("coverage_pct")) or 0) >= 100
        ),
        "last_map_mowed_at": _latest_iso(*(row.get("last_mowed_at") for row in public_rows)),
        "last_map_completed_at": (
            max(str(value) for value in completed_values if value)
            if public_rows and all(completed_values)
            else None
        ),
    }
    return state, public_rows, totals, task, events


def zone_rows_by_id(state_value: Any) -> dict[int, dict[str, Any]]:
    state = normalize_ledger_state(state_value)
    result: dict[int, dict[str, Any]] = {}
    for key, row in state["zones"].items():
        zone_id = as_int(row.get("id")) or as_int(key)
        if zone_id is not None and zone_id > 0:
            result[zone_id] = dict(row)
    return result
