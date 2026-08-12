"""Native Home Assistant diagnostics for Navimower."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .capability_profile import build_capability_profile
from .const import DOMAIN
from .diagnostics_sanitize import sanitize
from .private_cloud_region import private_cloud_region_diagnostics
from .maintenance_h5_discovery import probe_maintenance_h5
from .resume import resume_command_diagnostics


def _selected(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Return a compact copy of selected coordinator fields."""
    return {key: deepcopy(data.get(key)) for key in keys if key in data}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return a sanitized snapshot for Home Assistant Download diagnostics.

    Normal diagnostics use the config entry, coordinator state and caches.
    0.4.3-beta8 performs source-context candidate routing plus precise Parts maintenance and Mowing Reports call-site recovery
    within the bounded read-only public-H5 inspection; targeted candidates are reserved before broad fetching, and no mutation or report API request runs.
    """
    coordinator = (hass.data.get(DOMAIN) or {}).get(entry.entry_id)
    if coordinator is None:
        return {
            "format": "navimower-diagnostics-v2",
            "created_utc": datetime.now(UTC).isoformat(),
            "read_only": True,
            "diagnostics_source": "home_assistant_download",
            "note": "integration not loaded; only the stored entry is available",
            "entry": {
                "data": sanitize(dict(entry.data)),
                "options": sanitize(dict(entry.options)),
            },
        }

    data = deepcopy(coordinator.data or {})
    map_data = data.get("map") if isinstance(data.get("map"), dict) else {}
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
    maintenance = data.get("maintenance") if isinstance(data.get("maintenance"), dict) else {}
    raw_maintenance = raw.get("maintenance") if isinstance(raw.get("maintenance"), dict) else {}
    capabilities = data.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = build_capability_profile(data)

    mqtt_bridge = getattr(coordinator, "mqtt_bridge", None)
    mqtt_health = (
        mqtt_bridge.diagnostic_health()
        if mqtt_bridge is not None and hasattr(mqtt_bridge, "diagnostic_health")
        else None
    )
    private_polling = (
        coordinator.polling_diagnostics()
        if hasattr(coordinator, "polling_diagnostics")
        else None
    )
    problem_history = (
        coordinator.problem_diagnostics()
        if hasattr(coordinator, "problem_diagnostics")
        else None
    )
    notification_center = getattr(coordinator, "notification_center", None)
    notification_center_diagnostics = (
        notification_center.diagnostics()
        if notification_center is not None
        and hasattr(notification_center, "diagnostics")
        else None
    )

    history_index = (
        coordinator.history.sessions_index_payload()
        if getattr(coordinator, "history", None) is not None
        else {}
    )
    sessions = history_index.get("sessions") if isinstance(history_index, dict) else []
    sessions = sessions if isinstance(sessions, list) else []
    cycle = (
        coordinator.history.cycle_diagnostics()
        if getattr(coordinator, "history", None) is not None
        and hasattr(coordinator.history, "cycle_diagnostics")
        else None
    )

    try:
        maintenance_h5_discovery = await hass.async_add_executor_job(
            probe_maintenance_h5, coordinator.client
        )
    except Exception as err:  # noqa: BLE001 - optional beta diagnostics discovery
        maintenance_h5_discovery = {
            "ok": False, "read_only": True, "beta_only": True,
            "mutation_calls_executed": False,
            "error_type": type(err).__name__, "error": sanitize(str(err)),
        }

    return {
        "format": "navimower-diagnostics-v2",
        "created_utc": datetime.now(UTC).isoformat(),
        "read_only": True,
        "diagnostics_source": "home_assistant_download",
        "entry": {
            "data": sanitize(deepcopy(dict(entry.data))),
            "options": sanitize(deepcopy(dict(entry.options))),
        },
        "mower": sanitize(
            _selected(
                data,
                (
                    "name",
                    "model",
                    "vehicle_type",
                    "state",
                    "state_code",
                    "activity",
                    "docked",
                    "docked_source",
                    "error",
                    "error_text",
                    "error_code",
                    "error_title",
                    "error_content",
                    "error_kind",
                    "problem_source",
                    "last_problem",
                ),
            )
        ),
        "connectivity": sanitize(
            _selected(
                data,
                (
                    "private_cloud_connected",
                    "private_cloud_error",
                    "oauth_configured",
                    "oauth_connected",
                    "oauth_error",
                    "mqtt_configured",
                    "mqtt_connected",
                    "mqtt_error",
                    "mqtt_stream_state",
                    "mqtt_recovery_count",
                    "mqtt_vehicle_state",
                    "mqtt_state_age",
                    "mqtt_action",
                    "mqtt_action_age",
                ),
            )
        ),
        "private_cloud_region": sanitize(
            private_cloud_region_diagnostics(coordinator)
        ),
        "capabilities": sanitize(deepcopy(capabilities)),
        "maintenance": sanitize({"parsed": deepcopy(maintenance), "raw_component_maintenance": deepcopy(raw_maintenance)}),
        "maintenance_h5_discovery": maintenance_h5_discovery,
        "positioning": sanitize(
            _selected(
                data,
                (
                    "x",
                    "y",
                    "heading",
                    "pose_source",
                    "mqtt_pose_age",
                    "current_physical_zone",
                    "current_physical_zone_id",
                    "current_physical_zone_source",
                    "current_physical_zone_source_age",
                    "current_physical_zone_stale",
                    "current_channel",
                    "current_channel_id",
                    "current_channel_source",
                    "current_channel_pose_age",
                    "current_channel_stale",
                    "target_zone_ids",
                    "target_zone_source",
                ),
            )
        ),
        "telemetry": sanitize(
            _selected(
                data,
                (
                    "battery",
                    "battery_source",
                    "battery_source_age",
                    "battery_mqtt",
                    "battery_mqtt_age",
                    "battery_private_cloud",
                    "mowing_progress",
                    "mowing_progress_source",
                    "mowing_progress_source_age",
                    "task_progress_private_cloud",
                    "task_progress_source",
                    "active_zone_progress",
                    "active_zone_progress_source",
                    "active_zone_progress_zone_id",
                    "session_area",
                    "session_area_source",
                    "total_area",
                    "total_area_source",
                    "coverage",
                    "coverage_source",
                    "zone_states",
                    "totals",
                ),
            )
        ),
        "settings": sanitize(deepcopy(settings)),
        "map": sanitize(
            {
                "id": map_data.get("id"),
                "name": map_data.get("name"),
                "version": map_data.get("version"),
                "modified_count": map_data.get("modified_count"),
                "area": map_data.get("area"),
                "zone_count": len(map_data.get("zones") or []),
                "off_limit_count": len(map_data.get("off_limit_areas") or []),
                "vf_off_count": len(map_data.get("vf_off_areas") or []),
                "channel_count": len(map_data.get("channels") or []),
                "doodle_count": len(map_data.get("doodles") or []),
                "zone_details": deepcopy(data.get("zone_details") or []),
            }
        ),
        "history": sanitize(
            {
                "retained_session_count": len(sessions),
                "sessions": deepcopy(sessions),
                "cycle": deepcopy(cycle),
                "trail_active": data.get("trail_active"),
                "trail_point_count": len(data.get("trail") or []),
            }
        ),
        "problem_history": sanitize(deepcopy(problem_history)),
        "latest_notification": sanitize(
            {
                "title": data.get("notification_title"),
                "content": data.get("notification_content"),
                "created_at": data.get("notification_created_at"),
                "read": data.get("notification_read"),
                "level": data.get("notification_level"),
                "type": data.get("notification_type"),
                "style": data.get("notification_style"),
                "notification_code": data.get("notification_code"),
                "origin": data.get("notification_origin"),
                "kind": data.get("notification_kind"),
                "confidence": data.get("notification_confidence"),
                "count": data.get("notification_count"),
                "vendor_count": data.get("notification_vendor_count"),
                "local_count": data.get("notification_local_count"),
                "source": data.get("notification_source"),
                "source_age": data.get("notification_source_age"),
                "vendor_source_age": data.get("notification_vendor_source_age"),
                "last_error": data.get("notification_error"),
                "recent": deepcopy(data.get("notification_history") or []),
            }
        ),
        "notification_center": sanitize(deepcopy(notification_center_diagnostics)),
        "last_resume_command": sanitize(resume_command_diagnostics(coordinator)),
        "private_polling": sanitize(deepcopy(private_polling)),
        "mqtt_health": sanitize(deepcopy(mqtt_health)),
        "raw": sanitize(deepcopy(raw)),
        "notes": [
            "Normal diagnostics use current coordinator state and caches; 0.4.3-beta8 routes high-value H5 candidates into the targeted queue at discovery time and records the source context/reason for that routing.",
            "The beta H5 inspection sends no account or mower identity and executes no maintenance mutation or mower command.",
            "Private-cloud account region/host routing is separate from Smart Home OAuth/MQTT; MQTT continues to use the broker details returned by the official API.",
            "Capability profile entries are positive observations or narrow proven model constraints. An empty/missing endpoint in one snapshot is not treated as unsupported.",
            "Resume diagnostics record only the explicit command trace already held in memory; downloading diagnostics never sends Resume.",
            "The notification center keeps at most 10 vendor rows and 20 persistent Navimower-local rows, then merges them newest-first for Latest notification.",
            "Notification read actions are explicit Home Assistant services and are never executed by Download diagnostics.",
            "Account, mower, network and physical GPS identifiers are sanitized/redacted.",
            "Local map X/Y coordinates may remain because they are relative map geometry rather than GPS coordinates.",
        ],
    }
