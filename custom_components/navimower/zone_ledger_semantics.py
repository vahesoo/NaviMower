"""Authoritative ZoneLedger / CycleEngine projection.

The 0.5 cleanup runtime no longer computes a legacy zone model first. ZoneLedger
consumes captured vendor observations directly, owns current per-zone/task
state, and publishes the stable HA snapshot fields used by backend domains.
"""
from __future__ import annotations

from copy import deepcopy
import time
from typing import Any

from .zone_ledger import as_int, reduce_zone_ledger


def _zone_history_seed(
    snapshot: dict[str, Any],
    cycle_diagnostics: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build migration/history facts without making archived state authoritative."""
    result: dict[str, dict[str, Any]] = {}
    for row in snapshot.get("zone_details") or []:
        if not isinstance(row, dict):
            continue
        zone_id = as_int(row.get("id"))
        if zone_id is None or zone_id <= 0:
            continue
        result[str(zone_id)] = {
            "id": zone_id,
            "name": row.get("name"),
            "area_m2": row.get("area_m2"),
            "vendor_percentage": row.get("vendor_percentage"),
            "percentage": row.get("percentage"),
            "last_started_at": row.get("last_started_at"),
            "last_mowed_at": row.get("last_mowed_at"),
            "last_completed_at": row.get("last_completed_at"),
            "last_completed_progress": row.get("last_completed_progress"),
            "last_completed_source": row.get("last_completed_source"),
            "last_completed_confirmation": row.get("last_completed_confirmation"),
            "last_completed_cycle_id": row.get("last_completed_cycle_id"),
        }

    progress_state = (
        cycle_diagnostics.get("zone_progress_state")
        if isinstance(cycle_diagnostics, dict)
        else None
    )
    for key, state in (progress_state or {}).items():
        if not isinstance(state, dict):
            continue
        zone_id = as_int(key)
        if zone_id is None or zone_id <= 0:
            continue
        row = result.setdefault(str(zone_id), {"id": zone_id})
        # One-way upgrade seed for persisted History created before CycleEngine.
        row["migration_progress_pct"] = state.get("progress")
        row["migration_peak_progress_pct"] = state.get("peak_progress")
        row["migration_vendor_start_time"] = state.get("start_time")
    return result


def run_zone_ledger_authority(owner: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Reduce one captured snapshot and publish the sole current zone/task model."""
    map_payload = snapshot.get("map")
    map_zones = map_payload.get("zones") if isinstance(map_payload, dict) else []
    history = getattr(owner, "history", None)
    active_session = (
        history.active_session_metadata()
        if history is not None and hasattr(history, "active_session_metadata")
        else None
    )
    if not isinstance(active_session, dict):
        active_session = None

    cycle_diagnostics = (
        history.cycle_diagnostics()
        if history is not None and hasattr(history, "cycle_diagnostics")
        else None
    )
    snapshot["coverage_observation_id"] = (
        (getattr(owner, "_endpoint_status", {}).get("path_info_time") or {}).get(
            "last_success_mono"
        )
    )

    state, rows, totals, task, events = reduce_zone_ledger(
        getattr(owner, "_zone_ledger_state", None),
        snapshot=snapshot,
        map_zones=[
            dict(row) for row in map_zones or [] if isinstance(row, dict)
        ],
        zone_details=[
            dict(row)
            for row in snapshot.get("zone_details") or []
            if isinstance(row, dict)
        ],
        zone_history=_zone_history_seed(snapshot, cycle_diagnostics),
        active_session=active_session,
        observed_at_ms=int(time.time() * 1000),
    )

    owner._zone_ledger_state = state  # noqa: SLF001
    owner._zone_ledger_task = deepcopy(task)  # noqa: SLF001

    accept = getattr(owner, "_accept_vendor_observations", None)
    if accept is not None:
        accept(snapshot)

    diagnostics = {
        "mode": "authoritative",
        "public_owner": "ZoneLedger",
        "ledger_revision": as_int(state.get("revision")) or 0,
        "zone_count": len(rows),
        "task": deepcopy(task),
        "recent_events": deepcopy(events[-8:]),
        "cycle_owner": "ZoneLedger",
        "trail_owner": "VendorTrailStore",
    }
    owner._zone_ledger_diagnostics = diagnostics  # noqa: SLF001
    snapshot["zone_ledger"] = diagnostics

    snapshot["zone_states"] = deepcopy(rows)
    snapshot["zone_states_revision"] = as_int(state.get("revision")) or 0
    snapshot["totals"] = deepcopy(totals)
    snapshot["mowing_progress"] = task.get("progress_pct")
    snapshot["mowing_progress_source"] = (
        task.get("progress_source") or "cycle_engine"
    )
    snapshot["session_area"] = task.get("mowed_area_m2")
    snapshot["session_area_source"] = (
        task.get("mowed_area_source") or "cycle_engine"
    )
    snapshot["cycle_engine_owner"] = "ZoneLedger"

    return {
        "state": state,
        "rows": rows,
        "totals": totals,
        "task": task,
    }
