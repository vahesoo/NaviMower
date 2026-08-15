"""Pure decision helpers for Navimower-managed mowing windows."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Iterable


def parse_hhmm(value: Any, default: str) -> time:
    """Return a local wall-clock time from ``HH:MM`` or a time object."""
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = str(value or default).strip()
    try:
        hour_s, minute_s = text.split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
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


def _minimum_aware_datetime() -> datetime:
    return datetime.min.replace(tzinfo=datetime.now().astimezone().tzinfo)


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
        key = (0, _minimum_aware_datetime()) if parsed is None else (1, parsed)
        candidates.append(((key[0], key[1], zone_id), row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]
