"""Native Home Assistant diagnostics for Navimower."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .diagnostics_export import async_build_diagnostics, sanitize


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return sanitized read-only diagnostics for the Download diagnostics UI."""
    coordinator = (hass.data.get(DOMAIN) or {}).get(entry.entry_id)
    if coordinator is None:
        return {
            "format": "navimower-diagnostics-v2",
            "read_only": True,
            "note": "integration not loaded; only the stored entry is available",
            "entry": {
                "data": sanitize(dict(entry.data)),
                "options": sanitize(dict(entry.options)),
            },
        }

    document = await async_build_diagnostics(
        hass,
        coordinator,
        include_compressed_map=False,
    )

    # 0.4.1 keeps Home Assistant's native Download diagnostics as the single
    # supported diagnostics path. Remove the development-only inventories and
    # capture metadata used while reverse-engineering notifications and errors.
    for key in (
        "diagnostics_detail",
        "mqtt_inventory",
        "mqtt_discovery",
        "cloud_request_inventory",
        "last_mow_command",
        "state_transition_capture",
    ):
        document.pop(key, None)

    notes = document.get("notes")
    if isinstance(notes, list):
        document["notes"] = [
            note
            for note in notes
            if "Passive discovery" not in str(note)
            and "request inventory" not in str(note)
            and "command_status_at_export" not in str(note)
        ]

    # Keep a small production-facing notification summary without native app
    # jump URLs or any read-state mutation. The runtime sensor remains the
    # authoritative notification entity.
    data = coordinator.data or {}
    document["latest_notification"] = sanitize(
        deepcopy(
            {
                "title": data.get("notification_title"),
                "content": data.get("notification_content"),
                "created_at": data.get("notification_created_at"),
                "read": data.get("notification_read"),
                "level": data.get("notification_level"),
                "type": data.get("notification_type"),
                "style": data.get("notification_style"),
                "notification_code": data.get("notification_code"),
                "source": data.get("notification_source"),
                "source_age": data.get("notification_source_age"),
                "last_error": data.get("notification_error"),
            }
        )
    )
    document["diagnostics_source"] = "home_assistant_download"
    return document
