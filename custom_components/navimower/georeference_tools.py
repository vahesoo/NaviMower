"""Georeference field diagnostics and explicit relearn support."""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _xy(mapping: Any, x_key: str, y_key: str) -> tuple[float, float] | None:
    if not isinstance(mapping, dict):
        return None
    x = _float(mapping.get(x_key))
    y = _float(mapping.get(y_key))
    return (x, y) if x is not None and y is not None else None


def local_frame_diagnostics(coordinator: Any) -> dict[str, Any]:
    """Compare map-station, private-cloud and MQTT local coordinate frames.

    Station deltas are physically meaningful only while the mower is docked.
    Cloud-vs-MQTT deltas are included with their source timestamps/ages so stale
    samples are visible instead of being silently treated as simultaneous.
    """
    data = coordinator.data or {}
    raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
    cloud = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    mqtt = getattr(coordinator, "_mqtt_location", None)
    mqtt = mqtt if isinstance(mqtt, dict) else {}
    geometry = getattr(coordinator, "_map_geometry", None)
    geometry = geometry if isinstance(geometry, dict) else {}
    station = geometry.get("station") if isinstance(geometry.get("station"), dict) else {}

    station_xy = _xy(station, "x", "y")
    cloud_xy = _xy(cloud, "posture_x", "posture_y")
    mqtt_xy = _xy(mqtt, "x", "y")
    docked = bool(data.get("docked"))

    result: dict[str, Any] = {
        "map_revision": geometry.get("revision"),
        "docked": docked,
        "map_station": {
            "x": station_xy[0] if station_xy else None,
            "y": station_xy[1] if station_xy else None,
        },
        "private_cloud": {
            "x": cloud_xy[0] if cloud_xy else None,
            "y": cloud_xy[1] if cloud_xy else None,
            "report_time": cloud.get("report_time"),
        },
        "mqtt": {
            "x": mqtt_xy[0] if mqtt_xy else None,
            "y": mqtt_xy[1] if mqtt_xy else None,
            "pose_time": mqtt.get("pose_time"),
            "pose_age_s": data.get("mqtt_pose_age"),
        },
    }

    if cloud_xy and mqtt_xy:
        dx = cloud_xy[0] - mqtt_xy[0]
        dy = cloud_xy[1] - mqtt_xy[1]
        result["private_minus_mqtt"] = {
            "dx_m": round(dx, 4),
            "dy_m": round(dy, 4),
            "distance_m": round(math.hypot(dx, dy), 4),
        }

    if docked and station_xy:
        if cloud_xy:
            dx = cloud_xy[0] - station_xy[0]
            dy = cloud_xy[1] - station_xy[1]
            result["docked_private_minus_map_station"] = {
                "dx_m": round(dx, 4),
                "dy_m": round(dy, 4),
                "distance_m": round(math.hypot(dx, dy), 4),
            }
        if mqtt_xy:
            dx = mqtt_xy[0] - station_xy[0]
            dy = mqtt_xy[1] - station_xy[1]
            result["docked_mqtt_minus_map_station"] = {
                "dx_m": round(dx, 4),
                "dy_m": round(dy, 4),
                "distance_m": round(math.hypot(dx, dy), 4),
            }
    return result


async def async_relearn_georeference(coordinator: Any) -> dict[str, Any]:
    """Clear only learned georeference calibration for the current map revision."""
    geometry = getattr(coordinator, "_map_geometry", None)
    if not isinstance(geometry, dict):
        raise ValueError("Map geometry is not available yet")

    previous = deepcopy(geometry.get("georeference"))
    previous_calibration = deepcopy(geometry.get("_georeference_calibration"))
    vendor = geometry.get("_vendor_georeference")

    geometry.pop("_georeference_calibration", None)
    if isinstance(vendor, dict):
        geometry["georeference"] = deepcopy(vendor)
    else:
        geometry.pop("georeference", None)
    coordinator._map_dirty = True  # noqa: SLF001 - integration-owned maintenance action.

    try:
        await coordinator._state_store.async_save(coordinator._state_store_data())  # noqa: SLF001
    except Exception:  # persistence will also happen on the next normal refresh
        pass

    # A normal refresh collects the first new paired private-cloud XY/GPS sample.
    await coordinator.async_request_refresh()
    active = (coordinator.data or {}).get("georeference")
    return {
        "map_revision": geometry.get("revision"),
        "previous_source": (previous or {}).get("source") if isinstance(previous, dict) else None,
        "previous_sample_count": len((previous_calibration or {}).get("samples") or [])
        if isinstance(previous_calibration, dict)
        else 0,
        "active_status": (active or {}).get("status") if isinstance(active, dict) else None,
        "active_source": (active or {}).get("source") if isinstance(active, dict) else None,
    }
