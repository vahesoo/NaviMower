"""Explicit unredacted Navimower raw-data export for local development.

Unlike Home Assistant Download diagnostics, this action deliberately preserves
vendor payload values and full map data. It is intended for the integration
owner's local field research only and writes files below /config.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Callable

from homeassistant.core import HomeAssistant

from .georeference_tools import local_frame_diagnostics
from .map_identifiers import resolve_map_identifiers


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, default=repr),
        encoding="utf-8",
    )


def _call_capture(name: str, getter: Callable[[], Any]) -> dict[str, Any]:
    """Capture one read-only endpoint without making one failure lose the export."""
    try:
        return {"ok": True, "data": getter()}
    except Exception as err:  # noqa: BLE001 - diagnostics should retain partial data.
        return {"ok": False, "error": repr(err)}


def _fresh_private_payloads(coordinator: Any) -> dict[str, Any]:
    """Read all known non-mutating private-cloud endpoints with original values."""
    client = coordinator.client
    sn = coordinator.sn
    vehicle_type = coordinator.vehicle_type
    result: dict[str, Any] = {}
    reads: tuple[tuple[str, Callable[[], Any]], ...] = (
        ("auth_list", client.auth_list),
        ("index2", lambda: client.index2(sn)),
        ("device_info", lambda: client.device_info(sn)),
        ("set_list", lambda: client.set_list(sn)),
        ("vehicle_config", lambda: client.vehicle_config(sn)),
        ("today_plan", lambda: client.today_plan(sn, vehicle_type)),
        ("location", lambda: client.location(sn, vehicle_type)),
        ("errors", lambda: client.errors(sn, vehicle_type)),
        ("maintenance", lambda: client.maintenance(sn)),
        ("path_info_time", lambda: client.path_info_time(sn)),
        ("map_list", lambda: client.map_list(sn)),
    )
    for name, getter in reads:
        result[name] = _call_capture(name, getter)

    location = result.get("location", {}).get("data")
    map_list = result.get("map_list", {}).get("data")
    map_id, map_base_id, edit_time = resolve_map_identifiers(location, map_list)
    result["resolved_map_identifiers"] = {
        "map_id": map_id,
        "map_base_id": map_base_id,
        "edit_time": edit_time,
    }
    if map_id is not None and map_base_id is not None:
        result["map_detail_plain"] = _call_capture(
            "map_detail_plain",
            lambda: client.map_detail_plain(sn, str(map_id), str(map_base_id)),
        )
        result["map_detail_compress"] = _call_capture(
            "map_detail_compress",
            lambda: client.map_detail(sn, str(map_id), str(map_base_id)),
        )
        result["station_map"] = _call_capture(
            "station_map",
            lambda: client.station_map(sn, str(map_id), str(map_base_id)),
        )
    return result


async def async_export_raw_data(hass: HomeAssistant, coordinator: Any) -> str:
    """Write a full-value raw capture and return its local HA path."""
    private_payloads = await hass.async_add_executor_job(
        _fresh_private_payloads, coordinator
    )
    mqtt_bridge = getattr(coordinator, "mqtt_bridge", None)
    mqtt_raw = (
        mqtt_bridge.raw_message_diagnostics()
        if mqtt_bridge is not None and hasattr(mqtt_bridge, "raw_message_diagnostics")
        else None
    )
    document = {
        "format": "navimower-raw-data-v1",
        "created_utc": datetime.now(UTC).isoformat(),
        "warning": (
            "UNREDACTED DEVELOPMENT EXPORT. Contains exact vendor/map/location "
            "values and identifiers. Do not publish or attach publicly."
        ),
        "source": "navimower.export_raw_data",
        "entry_id": coordinator.entry.entry_id,
        "vehicle_sn": coordinator.sn,
        "vehicle_type": coordinator.vehicle_type,
        "private_cloud_fresh": private_payloads,
        "private_cloud_cached": deepcopy(getattr(coordinator, "_raw_cache", {})),
        "map_geometry_decoded": deepcopy(getattr(coordinator, "_map_geometry", None)),
        "map_cache_key": deepcopy(getattr(coordinator, "_map_cache_key", None)),
        "mqtt_raw_last_messages": deepcopy(mqtt_raw),
        "mqtt_parsed_cache": deepcopy(getattr(coordinator, "_mqtt_location", None)),
        "coordinator_snapshot": deepcopy(coordinator.data or {}),
        "local_frame_check": local_frame_diagnostics(coordinator),
        "endpoint_status": deepcopy(getattr(coordinator, "_endpoint_status", {})),
    }

    now = datetime.now(UTC)
    folder = Path(hass.config.path("navimower_diagnostics", "raw"))
    stamp = now.strftime("%Y%m%d_%H%M%S")
    path = folder / f"navimower_raw_{stamp}.json"
    latest = folder / "navimower_raw_latest.json"
    await hass.async_add_executor_job(_write_json, path, document)
    await hass.async_add_executor_job(_write_json, latest, document)
    return str(path)
