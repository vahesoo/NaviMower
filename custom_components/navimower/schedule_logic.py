"""Pure decision helpers for Navimower-managed mowing windows."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Iterable


def parse_hhmm(value: Any, default: str) -> time:
    """Return a local wall-clock time from ``HH:MM[:SS]`` or a time object."""
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = str(value or default).strip()
    try:
        parts = text.split(":")
        if len(parts) < 2:
            raise ValueError
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (TypeError, ValueError):
        hour_s, minute_s = default.split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
    return time(hour=hour, minute=minute)


def format_hhmm(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def window_state(now: datetime, start: time, end: time) -> tuple[bool, str | None]:
    """Return whether ``now`` is in the daily window and its stable start-date token."""
    local_time = now.timetz().replace(tzinfo=None)
    if start == end:
        return False, None
    if start < end:
        if start <= local_time < end:
            return True, now.date().isoformat()
        return False, None
    if local_time >= start:
        return True, now.date().isoformat()
    if local_time < end:
        return True, (now.date() - timedelta(days=1)).isoformat()
    return False, None


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


_MOW_START_ZONE_EVIDENCE_MAX_AGE_SECONDS = 60.0


def _zone_id(value: Any) -> int | None:
    try:
        zone_id = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return zone_id if zone_id > 0 else None


def _fresh_age(value: Any, *, limit: float = _MOW_START_ZONE_EVIDENCE_MAX_AGE_SECONDS) -> bool:
    try:
        age = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return 0.0 <= age <= limit


def _report_seconds(value: Any) -> float | None:
    try:
        stamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if stamp <= 0:
        return None
    return stamp / 1000.0 if stamp > 10_000_000_000 else stamp


def classify_schedule_mow_start(
    commanded_zone_id: Any,
    *,
    vendor_mowing_now: bool,
    vendor_mowing_at_send: bool | None,
    data: dict[str, Any],
    mqtt_location: dict[str, Any] | None,
    sent_at: Any,
) -> dict[str, Any]:
    """Classify a scheduler mow start without mistaking another retained task for success.

    A plain Docked/Idle -> Mowing transition is only a legacy fallback when no
    fresh zone evidence exists. Any fresh observed zone must agree with the
    scheduler command. A post-command MQTT work-target mismatch is strong
    evidence that the mower resumed a different retained task.
    """
    commanded = _zone_id(commanded_zone_id)
    if not vendor_mowing_now or commanded is None:
        return {"state": "pending", "observed_zone_ids": [], "strong_mismatch_zone_ids": []}

    observed: list[int] = []
    strong: list[int] = []

    active_zone = _zone_id(data.get("active_zone_progress_zone_id"))
    if active_zone is not None and _fresh_age(data.get("active_zone_progress_source_age")):
        observed.append(active_zone)

    immediate_zone = _zone_id(data.get("target_zone_id"))
    immediate_source = str(data.get("target_zone_immediate_source") or "")
    if (
        immediate_zone is not None
        and immediate_source == "mqtt_work_target"
        and _fresh_age(data.get("target_zone_immediate_age_seconds"))
    ):
        observed.append(immediate_zone)

    location = mqtt_location if isinstance(mqtt_location, dict) else {}
    sent = parse_iso(sent_at)
    report = _report_seconds(location.get("pose_time") or location.get("state_time"))
    mqtt_after_command = (
        sent is not None
        and report is not None
        and report >= sent.timestamp() - 1.0
        and _fresh_age(data.get("mqtt_action_age"))
    )
    if mqtt_after_command:
        for key in ("work_target_zone", "mow_boundary"):
            zone_id = _zone_id(location.get(key))
            if zone_id is not None:
                observed.append(zone_id)
                strong.append(zone_id)

    observed = list(dict.fromkeys(observed))
    strong = list(dict.fromkeys(strong))
    strong_mismatch = [zone_id for zone_id in strong if zone_id != commanded]
    if strong_mismatch:
        return {
            "state": "zone_mismatch",
            "observed_zone_ids": observed,
            "strong_mismatch_zone_ids": strong_mismatch,
        }

    if observed:
        return {
            "state": "confirmed" if all(zone_id == commanded for zone_id in observed) else "pending",
            "observed_zone_ids": observed,
            "strong_mismatch_zone_ids": [],
        }

    return {
        "state": "confirmed" if vendor_mowing_at_send is False else "pending",
        "observed_zone_ids": [],
        "strong_mismatch_zone_ids": [],
    }


def later_iso(first: Any, second: Any) -> str | None:
    """Return the later valid ISO timestamp."""
    a, b = parse_iso(first), parse_iso(second)
    if a is None and b is None:
        return None
    if a is None:
        return str(second)
    if b is None:
        return str(first)
    return str(second) if b > a else str(first)


def completion_advanced(current: Any, baseline: Any, dispatched_at: Any) -> bool:
    """Accept completion only when it is newer than both baseline and this dispatch."""
    current_dt = parse_iso(current)
    dispatch_dt = parse_iso(dispatched_at)
    if current_dt is None or dispatch_dt is None or current_dt <= dispatch_dt:
        return False
    baseline_dt = parse_iso(baseline)
    return baseline_dt is None or current_dt > baseline_dt


def filter_schedule_zones(
    zones: Iterable[dict[str, Any]],
    selected_zone_ids: Iterable[int],
    *,
    scheduler_completed_at: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return only user-selected zones with a confirmed successful completion."""
    selected: set[int] = set()
    for value in selected_zone_ids or []:
        try:
            selected.add(int(value))
        except (TypeError, ValueError):
            continue
    confirmed = scheduler_completed_at or {}
    result: list[dict[str, Any]] = []
    for row in zones or []:
        if not isinstance(row, dict):
            continue
        try:
            zone_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        if zone_id not in selected:
            continue
        effective = later_iso(row.get("last_completed_at"), confirmed.get(str(zone_id)))
        if parse_iso(effective) is None:
            continue
        result.append(row)
    return result


def select_oldest_zone(
    zones: Iterable[dict[str, Any]],
    *,
    completed_in_window: set[int] | None = None,
    just_completed_zone_id: int | None = None,
    scheduler_completed_at: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Choose the eligible zone whose confirmed completion is oldest."""
    completed = completed_in_window or set()
    confirmed = scheduler_completed_at or {}
    candidates: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for row in zones or []:
        if not isinstance(row, dict):
            continue
        try:
            zone_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        if zone_id <= 0 or zone_id in completed or zone_id == just_completed_zone_id:
            continue
        effective = later_iso(row.get("last_completed_at"), confirmed.get(str(zone_id)))
        parsed = parse_iso(effective)
        if parsed is None:
            continue
        candidates.append(((parsed, zone_id), row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]
