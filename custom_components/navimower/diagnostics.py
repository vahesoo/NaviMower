"""Native Home Assistant diagnostics for Navimower."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .diagnostics_export import async_build_diagnostics, inventory, sanitize

_NOTIFICATION_PATH = "/mowerbot/user/message/vehicleMessageListField"


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
    if hasattr(coordinator, "state_transition_diagnostics"):
        document["state_transition_capture"] = sanitize(
            coordinator.state_transition_diagnostics()
        )

    # Beta29 stops public H5 bundle discovery and directly probes the exact
    # read-only Notification -> Device feed recovered by beta28. The existing
    # private-cloud client supplies the authenticated/encrypted p:101 transport.
    # No read-state endpoint is called and no notification is marked read.
    try:
        response = await hass.async_add_executor_job(
            coordinator.client.notification_feed,
            coordinator.sn,
            "",
            "all",
        )
    except Exception as err:  # noqa: BLE001 - diagnostics records probe failure.
        document["notification_feed_probe"] = {
            "ok": False,
            "read_only": True,
            "endpoint": _NOTIFICATION_PATH,
            "request": {
                "message_id": "",
                "vehicle_sn": "<redacted>",
                "filter_state": "all",
            },
            "error_type": type(err).__name__,
            "error": sanitize(str(err)),
        }
    else:
        clean = sanitize(response)
        document["notification_feed_probe"] = {
            "ok": True,
            "read_only": True,
            "endpoint": _NOTIFICATION_PATH,
            "request": {
                "message_id": "",
                "vehicle_sn": "<redacted>",
                "filter_state": "all",
            },
            "response": clean,
            "inventory": inventory(clean),
        }

    document["diagnostics_source"] = "home_assistant_download"
    return document
