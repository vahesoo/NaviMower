"""Native Home Assistant diagnostics for Navimower."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .capability_profile import build_capability_profile
from .const import DOMAIN, OPT_GOOGLE_MAPS_API_KEY
from .diagnostics_sanitize import REDACTION_VERSION, sanitize
from .map_underlay import map_underlay_diagnostics
from .private_cloud_region import private_cloud_region_diagnostics
from .state_semantics import error_transition_diagnostics

# Omit retired research blocks even when an older runtime cache still has them.
# This is a report-only projection; it never changes or clears the source cache.
_RETIRED_RESEARCH_KEYS = frozenset({
    "maintenance_h5_discovery", "error_h5_discovery", "command_discovery",
})


def _selected(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Return a compact copy of selected coordinator fields."""
    return {key: deepcopy(data.get(key)) for key in keys if key in data}


def _diagnostic_options(entry: ConfigEntry) -> dict[str, Any]:
    """Return stored options without exporting secret API-key placeholders."""
    options = deepcopy(dict(entry.options))
    options.pop(OPT_GOOGLE_MAPS_API_KEY, None)
    return sanitize(options)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mqtt_navigation_diagnostics(coordinator: Any, data: dict[str, Any]) -> dict[str, Any]:
    """Return cached MQTT navigation fields needed to research gate timing."""
    location = getattr(coordinator, "_mqtt_location", None)
    location = location if isinstance(location, dict) else {}
    keys = (
        "vehicle_state", "action", "sub_action", "work_action", "work_sub_action",
        "work_mode", "work_target_zone", "mow_boundary", "partition_ids",
        "mow_progress", "work_progress", "mowing_percentage", "mow_start_type",
        "task_delay", "pose_time", "state_time",
    )
    cached = {key: deepcopy(location.get(key)) for key in keys if key in location}
    return {
        "cached_location": cached,
        "pose_age_s": data.get("mqtt_pose_age"),
        "action_age_s": data.get("mqtt_action_age"),
        "vehicle_state": data.get("mqtt_vehicle_state"),
        "physical_zone_id": data.get("current_physical_zone_id"),
        "physical_zone": data.get("current_physical_zone"),
        "physical_zone_source": data.get("current_physical_zone_source"),
        "physical_zone_source_age_s": data.get("current_physical_zone_source_age"),
        "target_zone_ids": deepcopy(data.get("target_zone_ids") or []),
        "target_zone_source": data.get("target_zone_source"),
        "zone_transition": data.get("zone_transition"),
        "gate_states": deepcopy(data.get("gate_states") or {}),
        "gate_arrival_guards": deepcopy(data.get("gate_arrival_guards") or {}),
    }


def _polygon_diagnostics(polygons: Any) -> list[dict[str, Any]]:
    """Return stable local-map geometry summaries for off-limit experiments."""
    result: list[dict[str, Any]] = []
    for index, polygon in enumerate(polygons or []):
        if not isinstance(polygon, list):
            continue
        points: list[list[float]] = []
        for point in polygon:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            x = _as_float(point[0])
            y = _as_float(point[1])
            if x is not None and y is not None:
                points.append([x, y])
        if len(points) < 3:
            continue
        cross_sum = 0.0
        centroid_x_sum = 0.0
        centroid_y_sum = 0.0
        for point_index, (x1, y1) in enumerate(points):
            x2, y2 = points[(point_index + 1) % len(points)]
            cross = x1 * y2 - x2 * y1
            cross_sum += cross
            centroid_x_sum += (x1 + x2) * cross
            centroid_y_sum += (y1 + y2) * cross
        signed_area = cross_sum / 2.0
        area = abs(signed_area)
        if abs(cross_sum) > 1e-9:
            centroid = [centroid_x_sum / (3.0 * cross_sum), centroid_y_sum / (3.0 * cross_sum)]
        else:
            centroid = [sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points)]
        result.append({
            "index": index,
            "point_count": len(points),
            "area_m2": round(area, 4),
            "centroid": [round(centroid[0], 4), round(centroid[1], 4)],
            "polygon": points,
        })
    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return a cached-only support report without invoking research or commands.

    Credential, account, network and geographic identifiers are redacted. Local
    X/Y geometry and operational state remain available for troubleshooting.
    The explicit unredacted development export is separate and unchanged.
    """
    coordinator = (hass.data.get(DOMAIN) or {}).get(entry.entry_id)
    if coordinator is None:
        report = {
            "format": "navimower-diagnostics-v2",
            "redaction_version": REDACTION_VERSION,
            "created_utc": datetime.now(UTC).isoformat(),
            "read_only": True,
            "diagnostics_source": "home_assistant_download",
            "cached_only": True,
            "note": "integration not loaded; only the stored entry is available",
            "entry": {"data": dict(entry.data), "options": _diagnostic_options(entry)},
        }
        return sanitize(report, sensitive_values=(entry.data, entry.options), exclude_keys=_RETIRED_RESEARCH_KEYS)

    data = deepcopy(coordinator.data or {})
    map_data = data.get("map") if isinstance(data.get("map"), dict) else {}
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
    raw_index2 = raw.get("index2") if isinstance(raw.get("index2"), dict) else {}
    raw_auth = raw.get("auth_item") if isinstance(raw.get("auth_item"), dict) else {}
    raw_location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    raw_for_diagnostics = deepcopy(raw)
    raw_for_diagnostics.pop("maintenance", None)

    capabilities = data.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = build_capability_profile(data)

    mqtt_bridge = getattr(coordinator, "mqtt_bridge", None)
    mqtt_health = (
        mqtt_bridge.diagnostic_health()
        if mqtt_bridge is not None and hasattr(mqtt_bridge, "diagnostic_health") else None
    )
    mqtt_inventory = (
        mqtt_bridge.diagnostic_inventory()
        if mqtt_bridge is not None and hasattr(mqtt_bridge, "diagnostic_inventory") else None
    )
    mqtt_discovery = (
        mqtt_bridge.diagnostic_discovery()
        if mqtt_bridge is not None and hasattr(mqtt_bridge, "diagnostic_discovery") else None
    )
    private_polling = coordinator.polling_diagnostics() if hasattr(coordinator, "polling_diagnostics") else None
    problem_history = coordinator.problem_diagnostics() if hasattr(coordinator, "problem_diagnostics") else None
    notification_center = getattr(coordinator, "notification_center", None)
    notification_center_diagnostics = (
        notification_center.diagnostics()
        if notification_center is not None and hasattr(notification_center, "diagnostics") else None
    )
    navimower_schedule = getattr(coordinator, "navimower_schedule", None)
    navimower_schedule_diagnostics = (
        navimower_schedule.diagnostics()
        if navimower_schedule is not None and hasattr(navimower_schedule, "diagnostics") else None
    )
    history_index = coordinator.history.sessions_index_payload() if getattr(coordinator, "history", None) is not None else {}
    sessions = history_index.get("sessions") if isinstance(history_index, dict) else []
    sessions = sessions if isinstance(sessions, list) else []
    cycle = (
        coordinator.history.cycle_diagnostics()
        if getattr(coordinator, "history", None) is not None and hasattr(coordinator.history, "cycle_diagnostics") else None
    )
    map_version = map_data.get("map_version") or raw_index2.get("mapVersion")
    edit_map_info = raw_index2.get("editMapInfo")
    if not isinstance(edit_map_info, dict):
        edit_map_info = {}

    report = {
        "format": "navimower-diagnostics-v2",
        "redaction_version": REDACTION_VERSION,
        "created_utc": datetime.now(UTC).isoformat(),
        "read_only": True,
        "diagnostics_source": "home_assistant_download",
        "cached_only": True,
        "entry": {"data": dict(entry.data), "options": _diagnostic_options(entry)},
        "mower": _selected(data, (
            "name", "model", "vehicle_type", "state", "state_code", "activity",
            "docked", "docked_source", "error", "error_text", "error_code",
            "error_title", "error_content", "error_kind", "problem_source", "last_problem",
        )),
        "connectivity": _selected(data, (
            "private_cloud_connected", "private_cloud_error", "oauth_configured",
            "oauth_connected", "oauth_error", "mqtt_configured", "mqtt_connected",
            "mqtt_error", "mqtt_stream_state", "mqtt_recovery_count", "mqtt_vehicle_state",
            "mqtt_state_age", "mqtt_action", "mqtt_action_age",
        )),
        "private_cloud_region": private_cloud_region_diagnostics(coordinator),
        "capabilities": capabilities,
        "positioning": _selected(data, (
            "x", "y", "heading", "pose_source", "mqtt_pose_age", "current_physical_zone",
            "current_physical_zone_id", "current_physical_zone_source",
            "current_physical_zone_source_age", "current_physical_zone_stale",
            "current_channel", "current_channel_id", "current_channel_source",
            "current_channel_pose_age", "current_channel_stale", "target_zone_ids", "target_zone_source",
        )),
        "mqtt_navigation": _mqtt_navigation_diagnostics(coordinator, data),
        "mqtt_inventory": mqtt_inventory,
        "mqtt_discovery": mqtt_discovery,
        "telemetry": _selected(data, (
            "battery", "battery_source", "battery_source_age", "battery_mqtt",
            "battery_mqtt_age", "battery_private_cloud", "mowing_progress",
            "mowing_progress_source", "mowing_progress_source_age", "task_progress_private_cloud",
            "task_progress_source", "active_zone_progress", "active_zone_progress_source",
            "active_zone_progress_source_age", "active_zone_progress_zone_id", "coverage_source_age",
            "session_area", "session_area_source", "total_area", "total_area_source",
            "coverage", "coverage_source", "zone_states", "totals",
        )),
        "settings": settings,
        "navimower_schedule": navimower_schedule_diagnostics,
        "georeference": data.get("georeference"),
        "map_underlay": map_underlay_diagnostics(coordinator),
        "map_edit": {
            "state_code": data.get("state_code"),
            "mqtt_vehicle_state": data.get("mqtt_vehicle_state"),
            "map_version": map_version,
            "location_map_edit_time": raw_location.get("map_edit_time"),
            "edit_map_info": edit_map_info,
            "edit_session_active": bool(str(edit_map_info.get("editMapUid") or "")),
        },
        "map": {
            "id": map_data.get("id"), "map_id": map_data.get("map_id"),
            "map_base_id": map_data.get("map_base_id"), "edit_time": map_data.get("edit_time"),
            "revision": map_data.get("revision"), "map_version": map_version,
            "name": map_data.get("name"), "version": map_data.get("version"),
            "modified_count": map_data.get("modified_count"), "area": map_data.get("area"),
            "zone_count": len(map_data.get("zones") or []),
            "off_limit_count": len(map_data.get("off_limit_areas") or []),
            "off_limit_areas": _polygon_diagnostics(map_data.get("off_limit_areas") or []),
            "vf_off_count": len(map_data.get("vf_off_areas") or []),
            "channel_count": len(map_data.get("channels") or []),
            "doodle_count": len(map_data.get("doodles") or []),
            "zone_details": deepcopy(data.get("zone_details") or []),
        },
        "history": {
            "retained_session_count": len(sessions), "sessions": sessions, "cycle": cycle,
            "trail_active": data.get("trail_active"), "trail_point_count": len(data.get("trail") or []),
        },
        "problem_history": problem_history,
        "error_investigation": {
            "policy": "private_cloud_canonical_mqtt_transition_trigger",
            "transition": error_transition_diagnostics(coordinator),
            "raw_index2_vehicle_state": raw_index2.get("vehicle_state"),
            "raw_auth_vehicle_state": raw_auth.get("vehicle_state"),
            "raw_index2_error_data": deepcopy(raw_index2.get("error_data") or []),
            "vendor_notification_raw_cache": deepcopy(getattr(coordinator, "_notification_raw_cache", None)),
            "vendor_notification_normalized_cache": deepcopy(getattr(coordinator, "_notification_cache", None)),
        },
        "latest_notification": {
            "title": data.get("notification_title"), "content": data.get("notification_content"),
            "created_at": data.get("notification_created_at"), "read": data.get("notification_read"),
            "level": data.get("notification_level"), "type": data.get("notification_type"),
            "style": data.get("notification_style"), "variable": deepcopy(data.get("notification_variable")),
            "notification_code": data.get("notification_code"), "origin": data.get("notification_origin"),
            "kind": data.get("notification_kind"), "confidence": data.get("notification_confidence"),
            "count": data.get("notification_count"), "vendor_count": data.get("notification_vendor_count"),
            "local_count": data.get("notification_local_count"), "source": data.get("notification_source"),
            "source_age": data.get("notification_source_age"), "vendor_source_age": data.get("notification_vendor_source_age"),
            "last_error": data.get("notification_error"), "recent": deepcopy(data.get("notification_history") or []),
        },
        "notification_center": notification_center_diagnostics,
        "last_resume_command": None,
        "private_polling": private_polling,
        "mqtt_health": mqtt_health,
        "raw": raw_for_diagnostics,
        "notes": [
            "Download diagnostics is cached-only: no extra vendor requests, commands or research crawls.",
            "Credential and identifying field names are matched across snake_case, camelCase and acronym variants.",
            "URL credentials, paths, query strings and fragments are omitted; only service origins remain.",
            "Georeference diagnostics retain local transform/validation context; geographic coordinates are redacted.",
            "Map underlay diagnostics retain availability/session status, not Google keys or session tokens.",
            "Local X/Y geometry, names and activity times remain useful support data: review them before sharing.",
            "The explicit navimower.export_raw_data development export is separate and remains unredacted.",
        ],
    }
    return sanitize(
        report,
        sensitive_values=(entry.data, entry.options, data, {"vehicle_sn": getattr(coordinator, "sn", None)}),
        exclude_keys=_RETIRED_RESEARCH_KEYS,
    )
