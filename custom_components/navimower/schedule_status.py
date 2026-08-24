"""UI-facing status snapshot for the Navimower-managed scheduler."""
from __future__ import annotations

from typing import Any

from .schedule_logic import later_iso, parse_iso


def _zone_id(row: dict[str, Any]) -> int | None:
    try:
        value = int(row.get("id"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _zone_name(row: dict[str, Any], zone_id: int) -> str:
    return str(row.get("name") or row.get("zone_name") or f"Zone {zone_id}")


def _ordered_remaining(
    eligible: list[dict[str, Any]],
    excluded: set[int],
    scheduler_completed_at: dict[str, str],
) -> list[int]:
    """Return the same oldest-completion-first order used by the scheduler."""
    candidates: list[tuple[Any, int]] = []
    for row in eligible:
        zone_id = _zone_id(row)
        if zone_id is None or zone_id in excluded:
            continue
        effective = later_iso(
            row.get("last_completed_at"), scheduler_completed_at.get(str(zone_id))
        )
        completed_at = parse_iso(effective)
        if completed_at is not None:
            candidates.append((completed_at, zone_id))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [zone_id for _, zone_id in candidates]


def schedule_status_snapshot(controller: Any) -> dict[str, Any]:
    """Build one stable, card-friendly snapshot without duplicating policy in UI."""
    diagnostics = controller.diagnostics()
    eligible = controller._eligible_zones()  # Scheduler-owned filtered zone model.
    by_id: dict[int, dict[str, Any]] = {}
    for row in eligible:
        zone_id = _zone_id(row)
        if zone_id is not None:
            by_id[zone_id] = row

    completed_ids: list[int] = []
    for raw in diagnostics.get("completed_zone_ids_in_window") or []:
        try:
            zone_id = int(raw)
        except (TypeError, ValueError):
            continue
        if zone_id in by_id and zone_id not in completed_ids:
            completed_ids.append(zone_id)

    active_id = diagnostics.get("active_zone_id")
    try:
        active_id = int(active_id) if active_id is not None else None
    except (TypeError, ValueError):
        active_id = None
    if active_id not in by_id:
        active_id = None

    excluded = set(completed_ids)
    if active_id is not None:
        excluded.add(active_id)
    remaining_ids = _ordered_remaining(
        eligible,
        excluded,
        diagnostics.get("scheduler_completed_at") or {},
    )

    if diagnostics.get("order_mode") == "custom":
        completed_slots = {int(v) for v in diagnostics.get("completed_queue_slots") or []}
        active_slot = diagnostics.get("active_queue_slot")
        custom_queue = diagnostics.get("custom_queue") or []
        custom_items = []
        for slot, raw in enumerate(custom_queue):
            try: zone_id = int(raw)
            except (TypeError, ValueError): continue
            if zone_id not in by_id: continue
            status = "completed" if slot in completed_slots else ("active" if active_slot is not None and int(active_slot) == slot else "upcoming")
            custom_items.append({"slot": slot, "id": zone_id, "name": _zone_name(by_id[zone_id], zone_id), "status": status})
        queue = custom_items
    else:
        queue = []
    for zone_id in ([] if diagnostics.get("order_mode") == "custom" else completed_ids):
        queue.append({"id": zone_id, "name": _zone_name(by_id[zone_id], zone_id), "status": "completed"})
    if active_id is not None and diagnostics.get("order_mode") != "custom":
        queue.append({"id": active_id, "name": _zone_name(by_id[active_id], active_id), "status": "active"})
    for zone_id in ([] if diagnostics.get("order_mode") == "custom" else remaining_ids):
        queue.append({"id": zone_id, "name": _zone_name(by_id[zone_id], zone_id), "status": "upcoming"})

    suspended_reason = diagnostics.get("suspended_reason")
    if not diagnostics.get("enabled"):
        state = "off"
    elif suspended_reason:
        state = "suspended"
    elif active_id is not None:
        state = "running"
    elif diagnostics.get("window_open"):
        state = "waiting"
    else:
        state = "outside_window"

    active = next((item for item in queue if item["status"] == "active"), None)
    upcoming = [item for item in queue if item["status"] == "upcoming"]
    completed = [item for item in queue if item["status"] == "completed"]
    return {
        "state": state,
        "enabled": bool(diagnostics.get("enabled")),
        "configured": bool(controller.configured),
        "mode": diagnostics.get("mode"),
        "order_mode": diagnostics.get("order_mode") or "automatic",
        "custom_queue": diagnostics.get("custom_queue") or [],
        "active_queue_slot": diagnostics.get("active_queue_slot"),
        "completed_queue_slots": diagnostics.get("completed_queue_slots") or [],
        "start": diagnostics.get("start"),
        "end": diagnostics.get("end"),
        "window_open": bool(diagnostics.get("window_open")),
        "round_index": diagnostics.get("round_index") or 1,
        "queue": queue,
        "completed_zones": completed,
        "active_zone": active,
        "next_zone": upcoming[0] if upcoming else None,
        "upcoming_zones": upcoming,
        "selected_zone_ids": diagnostics.get("selected_zone_ids") or [],
        "eligible_zone_ids": diagnostics.get("eligible_zone_ids") or [],
        "resume_pending": bool(diagnostics.get("resume_pending")),
        "interrupted_zone_id": diagnostics.get("interrupted_zone_id"),
        "last_command": diagnostics.get("last_command"),
        "last_error": diagnostics.get("last_error"),
        "suspended_reason": suspended_reason,
    }
