"""Project Canonical Mower State directly onto the public HA snapshot."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .canonical_state import build_canonical_state, canonical_diagnostics
from .const import MQTT_POSE_STALE_SECONDS
from .position_fallback import cloud_report_age


def _apply_public_state(snapshot: dict[str, Any], state: dict[str, Any]) -> None:
    """Project canonical resolved values onto stable HA-facing fields."""
    navigation = (
        state.get("navigation")
        if isinstance(state.get("navigation"), dict)
        else {}
    )
    position = (
        navigation.get("position")
        if isinstance(navigation.get("position"), dict)
        else {}
    )
    value = position.get("value")
    if isinstance(value, dict):
        snapshot["position"] = deepcopy(value)
        snapshot["pose_source"] = position.get("source")
        snapshot["position_source_age"] = position.get("source_age_s")
        snapshot["position_stale"] = bool(position.get("stale"))
    else:
        snapshot["position"] = None
        snapshot["pose_source"] = position.get("source")
        snapshot["position_source_age"] = position.get("source_age_s")
        snapshot["position_stale"] = True

    for key, target in (
        ("physical_zone_id", "current_physical_zone_id"),
        ("physical_zone_source", "current_physical_zone_source"),
        ("target_zone_id", "target_zone_id"),
        ("target_zone_source", "target_zone_source"),
        ("planned_zone_ids", "planned_zone_ids"),
        ("planned_zones_source", "planned_zones_source"),
        ("channel_id", "current_channel_id"),
        ("channel_source", "current_channel_source"),
    ):
        if key in navigation:
            snapshot[target] = deepcopy(navigation.get(key))

    task = state.get("task") if isinstance(state.get("task"), dict) else {}
    totals = snapshot.get("totals")
    if not isinstance(totals, dict):
        totals = {}
        snapshot["totals"] = totals
    totals["task_progress_pct"] = task.get("progress_pct")
    totals["task_mowed_area_m2"] = task.get("mowed_area_m2")
    totals["task_area_m2"] = task.get("area_m2")
    totals["task_zone_ids"] = deepcopy(task.get("zone_ids") or [])
    totals["active_zone_id"] = task.get("active_zone_id")
    totals["task_progress_source"] = task.get("progress_source")
    totals["task_mowed_area_source"] = task.get("mowed_area_source")

    # Stable internal/public telemetry names remain projections of Canonical,
    # not independent resolvers.
    snapshot["mowing_progress"] = task.get("progress_pct")
    snapshot["mowing_progress_source"] = (
        task.get("progress_source") or "canonical_task"
    )
    snapshot["session_area"] = task.get("mowed_area_m2")
    snapshot["session_area_source"] = (
        task.get("mowed_area_source") or "canonical_task"
    )

    snapshot["canonical_owner"] = "canonical_mower_state"
    snapshot["canonical_schema_version"] = state.get("schema_version")
    snapshot["canonical_mode"] = state.get("mode")


def _cloud_report_time(snapshot: dict[str, Any]) -> Any:
    raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
    location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    if location.get("report_time") is not None:
        return location.get("report_time")
    if snapshot.get("pose_source") == "private_cloud":
        return snapshot.get("pose_time")
    return None


def run_canonical_authority(owner: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Resolve and publish Canonical state once after CycleEngine."""
    mqtt_position = (
        owner._fresh_mqtt_position()
        if hasattr(owner, "_fresh_mqtt_position")
        else None
    )
    cloud_position = snapshot.get("cloud_position")
    cloud_age = cloud_report_age(_cloud_report_time(snapshot))
    store = getattr(owner, "vendor_trail_store", None)
    owned_zone_ids = (
        set(store.owned_zone_ids())
        if store is not None and hasattr(store, "owned_zone_ids")
        else set()
    )
    store_revision = getattr(store, "revision", None) if store is not None else None

    state = build_canonical_state(
        snapshot,
        ledger_state=getattr(owner, "_zone_ledger_state", None),
        ledger_task=getattr(owner, "_zone_ledger_task", None),
        vendor_owned_zone_ids=owned_zone_ids,
        vendor_store_revision=store_revision,
        mqtt_position=mqtt_position,
        cloud_position=cloud_position,
        cloud_position_age_s=cloud_age,
        mqtt_pose_max_age_s=float(MQTT_POSE_STALE_SECONDS),
    )
    _apply_public_state(snapshot, state)
    owner._canonical_state = state
    owner._canonical_diagnostics = canonical_diagnostics(state)
    return state
