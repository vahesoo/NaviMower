"""Persistent state for continuing the last explicit ordered mowing run.

The vendor Resume command can retain mowing progress but may choose its own zone
order.  Navimower therefore remembers the last successfully sent ordered zone
list separately from the live vendor task.  A later continue action can remove
zones that were confirmed complete and send only the unfinished remainder with
the vendor continue (reset=false) mowing command.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

ORDERED_RUN_SCHEMA_VERSION = 1
CONFIRMED_COMPLETION_PCT = 100.0


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _zone_ids(values: Any) -> list[int]:
    result: list[int] = []
    for raw in values or []:
        zone_id = _as_int(raw)
        if zone_id is not None and zone_id > 0 and zone_id not in result:
            result.append(zone_id)
    return result


def _iso_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    try:
        return int(parsed.timestamp() * 1000)
    except (OverflowError, OSError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _rows_by_id(zone_states: Any) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    for item in zone_states or []:
        if not isinstance(item, dict):
            continue
        zone_id = _as_int(item.get("id"))
        if zone_id is not None and zone_id > 0:
            rows[zone_id] = item
    return rows


def normalize_last_ordered_run(value: Any) -> dict[str, Any] | None:
    """Return a safe persisted ordered-run record or None."""
    if not isinstance(value, dict):
        return None
    zone_ids = _zone_ids(value.get("zone_ids"))
    started_at = str(value.get("started_at") or "").strip()
    if not zone_ids or _iso_ms(started_at) is None:
        return None

    completed_set = set(_zone_ids(value.get("completed_zone_ids")))
    completed = [zone_id for zone_id in zone_ids if zone_id in completed_set]
    remaining = [zone_id for zone_id in zone_ids if zone_id not in completed_set]
    superseded_at = str(value.get("superseded_at") or "").strip() or None

    result = {
        "schema_version": ORDERED_RUN_SCHEMA_VERSION,
        "started_at": started_at,
        "source": str(value.get("source") or "unknown"),
        "zone_ids": zone_ids,
        "reset": bool(value.get("reset")),
        "completed_zone_ids": completed,
        "remaining_zone_ids": remaining,
        "complete": not remaining,
        "resumable": bool(remaining) and superseded_at is None,
        "superseded_at": superseded_at,
        "superseded_by": (
            str(value.get("superseded_by") or "").strip() or None
        ),
        "continue_count": max(0, _as_int(value.get("continue_count")) or 0),
        "last_continue_at": (
            str(value.get("last_continue_at") or "").strip() or None
        ),
        "last_continue_zone_ids": _zone_ids(value.get("last_continue_zone_ids")),
        "last_updated_at": (
            str(value.get("last_updated_at") or "").strip() or started_at
        ),
    }
    model = str(value.get("model") or "").strip()
    if model:
        result["model"] = model
    return result


def start_last_ordered_run(
    *,
    zone_ids: list[int],
    zone_states: Any,
    reset: bool,
    source: str,
    started_at: str,
    model: str | None = None,
) -> dict[str, Any]:
    """Create a tracker for one successfully sent explicit ordered run.

    A reset=false command is allowed to inherit zones that were already at a
    confirmed 100% before the command.  A reset=true command deliberately does
    not inherit that old completion because vendor reset propagation can lag.
    """
    ordered = _zone_ids(zone_ids)
    if not ordered:
        raise ValueError("ordered run requires at least one zone")
    if _iso_ms(started_at) is None:
        raise ValueError("ordered run requires a valid started_at timestamp")

    rows = _rows_by_id(zone_states)
    baseline_completed: list[int] = []
    if not reset:
        for zone_id in ordered:
            row = rows.get(zone_id) or {}
            coverage = _as_float(row.get("coverage_pct"))
            if coverage is not None and coverage >= CONFIRMED_COMPLETION_PCT:
                baseline_completed.append(zone_id)

    now = _now_iso()
    record: dict[str, Any] = {
        "schema_version": ORDERED_RUN_SCHEMA_VERSION,
        "started_at": started_at,
        "source": str(source),
        "zone_ids": ordered,
        "reset": bool(reset),
        "completed_zone_ids": baseline_completed,
        "remaining_zone_ids": [
            zone_id for zone_id in ordered if zone_id not in baseline_completed
        ],
        "complete": len(baseline_completed) == len(ordered),
        "resumable": len(baseline_completed) != len(ordered),
        "superseded_at": None,
        "superseded_by": None,
        "continue_count": 0,
        "last_continue_at": None,
        "last_continue_zone_ids": [],
        "last_updated_at": now,
    }
    if model:
        record["model"] = str(model)
    return record


def update_last_ordered_run(
    value: Any,
    *,
    zone_states: Any,
) -> dict[str, Any] | None:
    """Add zones whose confirmed completion timestamp belongs to this run."""
    current = normalize_last_ordered_run(value)
    if current is None:
        return None

    started_ms = _iso_ms(current["started_at"])
    if started_ms is None:
        return current

    completed = set(current["completed_zone_ids"])
    rows = _rows_by_id(zone_states)
    for zone_id in current["zone_ids"]:
        row = rows.get(zone_id) or {}
        completed_ms = _iso_ms(row.get("last_completed_at"))
        if completed_ms is not None and completed_ms >= started_ms:
            completed.add(zone_id)

    ordered_completed = [
        zone_id for zone_id in current["zone_ids"] if zone_id in completed
    ]
    remaining = [
        zone_id for zone_id in current["zone_ids"] if zone_id not in completed
    ]
    if (
        ordered_completed == current["completed_zone_ids"]
        and remaining == current["remaining_zone_ids"]
    ):
        return current

    current["completed_zone_ids"] = ordered_completed
    current["remaining_zone_ids"] = remaining
    current["complete"] = not remaining
    current["resumable"] = bool(remaining) and current.get("superseded_at") is None
    current["last_updated_at"] = _now_iso()
    return current


def record_ordered_run_continue(
    value: Any,
    *,
    zone_ids: list[int],
    at: str | None = None,
) -> dict[str, Any] | None:
    """Record one continue command without replacing the original ordered list."""
    current = normalize_last_ordered_run(value)
    if current is None:
        return None
    current["continue_count"] = int(current.get("continue_count") or 0) + 1
    current["last_continue_at"] = at or _now_iso()
    current["last_continue_zone_ids"] = _zone_ids(zone_ids)
    current["last_updated_at"] = current["last_continue_at"]
    return current


def supersede_last_ordered_run(
    value: Any,
    *,
    source: str,
    at: str | None = None,
) -> dict[str, Any] | None:
    """Keep the prior run for diagnostics but make it unsafe to continue."""
    current = normalize_last_ordered_run(value)
    if current is None:
        return None
    current["superseded_at"] = at or _now_iso()
    current["superseded_by"] = str(source)
    current["resumable"] = False
    current["last_updated_at"] = current["superseded_at"]
    return current


def last_ordered_run_snapshot(value: Any) -> dict[str, Any] | None:
    """Return an isolated public/diagnostic copy."""
    normalized = normalize_last_ordered_run(value)
    return deepcopy(normalized) if normalized is not None else None


__all__ = [
    "CONFIRMED_COMPLETION_PCT",
    "ORDERED_RUN_SCHEMA_VERSION",
    "last_ordered_run_snapshot",
    "normalize_last_ordered_run",
    "record_ordered_run_continue",
    "start_last_ordered_run",
    "supersede_last_ordered_run",
    "update_last_ordered_run",
]
