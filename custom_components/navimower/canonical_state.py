"""Canonical Mower State for the 0.5 architecture.

This module resolves transport observations and CycleEngine state into one
authoritative model consumed by public entities and the paired Map API contract.
History remains a separate archive owner.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

CANONICAL_SCHEMA_VERSION = 1
CANONICAL_MODE = "authoritative_v2"
DEFAULT_MQTT_POSE_MAX_AGE_S = 20.0
DEFAULT_CLOUD_POSITION_MAX_AGE_S = 90.0


def _as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _position(value: Any) -> dict[str, float | None] | None:
    if not isinstance(value, dict):
        return None
    x = _as_float(value.get("x"))
    y = _as_float(value.get("y"))
    if x is None or y is None:
        return None
    return {"x": x, "y": y, "heading": _as_float(value.get("heading"))}


def resolve_position(
    *,
    mqtt_position: Any,
    mqtt_pose_age_s: Any,
    cloud_position: Any,
    cloud_position_age_s: Any,
    station_position: Any = None,
    docked: bool = False,
    pending_activity: Any = None,
    mqtt_pose_max_age_s: float = DEFAULT_MQTT_POSE_MAX_AGE_S,
    cloud_position_max_age_s: float = DEFAULT_CLOUD_POSITION_MAX_AGE_S,
) -> dict[str, Any]:
    """Resolve position once, keeping source/freshness beside the value."""
    mqtt = _position(mqtt_position)
    cloud = _position(cloud_position)
    station = _position(station_position)
    mqtt_age = _as_float(mqtt_pose_age_s)
    cloud_age = _as_float(cloud_position_age_s)

    if mqtt is not None and (mqtt_age is None or mqtt_age <= mqtt_pose_max_age_s):
        value, source, age, stale = mqtt, "official_mqtt", mqtt_age, False
    elif docked is True and pending_activity is None and station is not None:
        # A confirmed stationary Dock state and the map's charging-pile position
        # are stronger evidence than a private-cloud posture that may be an old
        # last-known coordinate returned by a newly fetched get-location call.
        value, source, age, stale = station, "map_station", None, False
    elif cloud is not None:
        value, source, age = cloud, "private_cloud", cloud_age
        stale = bool(cloud_age is not None and cloud_age > cloud_position_max_age_s)
    else:
        value, source, age, stale = None, "unavailable", None, True

    return {
        "value": value,
        "source": source,
        "source_age_s": round(age, 3) if age is not None else None,
        "stale": stale,
        "available": value is not None,
    }


def _canonical_task(ledger_task: dict[str, Any] | None) -> dict[str, Any]:
    """Project the task owned by ZoneLedger; there is no legacy totals bridge."""
    task = ledger_task if isinstance(ledger_task, dict) else {}
    return {
        "progress_pct": task.get("progress_pct"),
        "mowed_area_m2": task.get("mowed_area_m2"),
        "area_m2": task.get("area_m2"),
        "zone_ids": deepcopy(task.get("zone_ids") or []),
        "active_zone_id": task.get("active_zone_id"),
        "progress_source": task.get("progress_source"),
        "mowed_area_source": task.get("mowed_area_source"),
        "source": "zone_ledger_task",
    }


def _canonical_cycles(
    ledger_state: dict[str, Any] | None,
    vendor_owned_zone_ids: set[int],
) -> list[dict[str, Any]]:
    zones = ledger_state.get("zones") if isinstance(ledger_state, dict) else {}
    rows: list[dict[str, Any]] = []
    for key, value in (zones or {}).items():
        if not isinstance(value, dict):
            continue
        zone_id = _as_int(value.get("id"))
        if zone_id is None:
            zone_id = _as_int(key)
        if zone_id is None or zone_id <= 0:
            continue
        rows.append({
            "zone_id": zone_id,
            "cycle_id": value.get("cycle_key"),
            "progress_pct": value.get("progress_pct"),
            "mowed_area_m2": value.get("mowed_area_m2"),
            "completed": value.get("completed_current_cycle"),
            "pending_vendor_cycle": bool(value.get("pending_vendor_cycle")),
            "progress_source": value.get("progress_source"),
            "source_age_s": value.get("source_age_s"),
            "stale": bool(value.get("stale")),
            "vendor_geometry_owned": zone_id in vendor_owned_zone_ids,
        })
    return sorted(rows, key=lambda row: int(row["zone_id"]))


def build_canonical_state(
    snapshot: dict[str, Any],
    *,
    ledger_state: dict[str, Any] | None = None,
    ledger_task: dict[str, Any] | None = None,
    vendor_owned_zone_ids: set[int] | None = None,
    vendor_store_revision: int | None = None,
    mqtt_position: Any = None,
    cloud_position: Any = None,
    cloud_position_age_s: Any = None,
    station_position: Any = None,
    pending_activity: Any = None,
    mqtt_pose_max_age_s: float = DEFAULT_MQTT_POSE_MAX_AGE_S,
) -> dict[str, Any]:
    """Build the authoritative canonical state from resolved observations."""
    owned = set(vendor_owned_zone_ids or set())
    mqtt_age = _as_float(snapshot.get("mqtt_pose_age"))
    cloud_age = _as_float(cloud_position_age_s)
    position = resolve_position(
        mqtt_position=mqtt_position,
        mqtt_pose_age_s=mqtt_age,
        cloud_position=cloud_position,
        cloud_position_age_s=cloud_age,
        station_position=station_position,
        docked=snapshot.get("docked") is True,
        pending_activity=pending_activity,
        mqtt_pose_max_age_s=mqtt_pose_max_age_s,
    )
    task = _canonical_task(ledger_task)
    cycles = _canonical_cycles(ledger_state, owned)
    mqtt_pose_seen = mqtt_age is not None
    mqtt_pose_fresh = _position(mqtt_position) is not None and (
        mqtt_age is None or mqtt_age <= mqtt_pose_max_age_s
    )
    fallback_reason = None
    if position.get("source") == "map_station":
        fallback_reason = "docked_station_authority"
    elif position.get("source") == "private_cloud":
        if mqtt_age is None:
            fallback_reason = "mqtt_pose_missing"
        elif mqtt_age > mqtt_pose_max_age_s:
            fallback_reason = "mqtt_pose_stale"
        elif not mqtt_pose_fresh:
            fallback_reason = "mqtt_pose_unavailable"

    map_payload = snapshot.get("map") if isinstance(snapshot.get("map"), dict) else {}
    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "mode": CANONICAL_MODE,
        "public_owner": "canonical_mower_state",
        "owners": {
            "position": "PositionResolver",
            "device_state": "CanonicalDeviceState",
            "navigation": "NavigationResolver",
            "task": "TaskResolver",
            "cycles": "CycleEngine",
            "history": "History",
            "vendor_geometry": "CycleEngine",
        },
        "device": {
            "activity": snapshot.get("activity"),
            "state": snapshot.get("state"),
            "state_code": snapshot.get("state_code"),
            "docked": snapshot.get("docked"),
            "error": snapshot.get("error"),
            "source": "canonical_bridge",
        },
        "connectivity": {
            "private_cloud_connected": snapshot.get("private_cloud_connected"),
            "mqtt_connected": snapshot.get("mqtt_connected"),
            "mqtt_stream_state": snapshot.get("mqtt_stream_state"),
        },
        "navigation": {
            "position": position,
            "physical_zone_id": snapshot.get("current_physical_zone_id"),
            "physical_zone_source": snapshot.get("current_physical_zone_source"),
            "target_zone_id": snapshot.get("target_zone_id"),
            "target_zone_source": snapshot.get("target_zone_source"),
            "planned_zone_ids": deepcopy(snapshot.get("planned_zone_ids") or []),
            "planned_zones_source": snapshot.get("planned_zones_source"),
            "channel_id": snapshot.get("current_channel_id"),
            "channel_source": snapshot.get("current_channel_source"),
        },
        "task": task,
        "map": {
            "revision": map_payload.get("revision"),
            "zone_count": len(map_payload.get("zones") or []),
        },
        "cycles": {
            "ledger_revision": _as_int((ledger_state or {}).get("revision")) or 0,
            "vendor_store_revision": vendor_store_revision,
            "rows": cycles,
        },
        "health": {
            "mqtt_pose_age_s": round(mqtt_age, 3) if mqtt_age is not None else None,
            "mqtt_pose_seen": mqtt_pose_seen,
            "mqtt_pose_available": mqtt_pose_fresh,
            "mqtt_pose_sparse_or_stale": bool(mqtt_age is not None and mqtt_age > mqtt_pose_max_age_s),
            "cloud_position_age_s": round(cloud_age, 3) if cloud_age is not None else None,
            "position_fallback_reason": fallback_reason,
            "private_cloud_position_fallback_active": position.get("source") == "private_cloud",
            "map_station_position_override_active": position.get("source") == "map_station",
        },
    }


def canonical_diagnostics(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a privacy-safe cached projection; exact X/Y never leave here."""
    if not isinstance(state, dict):
        return None
    navigation = state.get("navigation") if isinstance(state.get("navigation"), dict) else {}
    position = navigation.get("position") if isinstance(navigation.get("position"), dict) else {}
    cycle_block = state.get("cycles") if isinstance(state.get("cycles"), dict) else {}
    cycles = cycle_block.get("rows") if isinstance(cycle_block.get("rows"), list) else []
    vendor_owned = [row.get("zone_id") for row in cycles if isinstance(row, dict) and row.get("vendor_geometry_owned") is True]
    pending = [row.get("zone_id") for row in cycles if isinstance(row, dict) and row.get("pending_vendor_cycle") is True]
    return {
        "schema_version": state.get("schema_version"),
        "mode": state.get("mode"),
        "public_owner": state.get("public_owner"),
        "owners": deepcopy(state.get("owners") or {}),
        "position": {
            "available": position.get("available"),
            "source": position.get("source"),
            "source_age_s": position.get("source_age_s"),
            "stale": position.get("stale"),
            "coordinates_included": False,
        },
        "task": deepcopy(state.get("task") or {}),
        "cycles": {
            "ledger_revision": cycle_block.get("ledger_revision"),
            "vendor_store_revision": cycle_block.get("vendor_store_revision"),
            "zone_count": len(cycles),
            "vendor_geometry_owned_zone_ids": vendor_owned,
            "pending_vendor_cycle_zone_ids": pending,
        },
        "health": deepcopy(state.get("health") or {}),
    }
