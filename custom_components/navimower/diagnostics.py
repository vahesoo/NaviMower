"""Native Home Assistant diagnostics for Navimower."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .diagnostics_export import async_build_diagnostics, inventory, sanitize
from .notification_feed_discovery import probe_main_notification_feed

_NOTIFICATION_PATH = "/mowerbot/user/message/get-vehicle-history-message"


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

    # Keep the exact beta26 vehicle-history contract visible while beta27
    # investigates whether the main Notification -> Device feed is a different
    # request path. The vendor call is read-only and uses the existing p:101
    # client; no message is marked read.
    try:
        response = await hass.async_add_executor_job(
            coordinator.client.notification_history,
            coordinator.sn,
            1,
            20,
        )
    except Exception as err:  # noqa: BLE001 - diagnostics records probe failure.
        document["notification_history_probe"] = {
            "ok": False,
            "read_only": True,
            "endpoint": _NOTIFICATION_PATH,
            "request": {
                "vehicle_sn": "<redacted>",
                "page": 1,
                "size": 20,
            },
            "error_type": type(err).__name__,
            "error": str(err),
        }
    else:
        clean = sanitize(response)
        document["notification_history_probe"] = {
            "ok": True,
            "read_only": True,
            "endpoint": _NOTIFICATION_PATH,
            "request": {
                "vehicle_sn": "<redacted>",
                "page": 1,
                "size": 20,
            },
            "response": clean,
            "inventory": inventory(clean),
        }

    # Beta27 re-enters public H5 discovery, but only around exact strings from
    # the main Notification UI. This scanner never receives credentials or the
    # mower serial and persists only bounded structural context.
    try:
        discovery = await hass.async_add_executor_job(
            probe_main_notification_feed,
            coordinator.client,
        )
    except Exception as err:  # noqa: BLE001 - optional diagnostics discovery.
        document["notification_feed_discovery"] = {
            "ok": False,
            "read_only": True,
            "error_type": type(err).__name__,
            "error": sanitize(str(err)),
        }
    else:
        document["notification_feed_discovery"] = sanitize(discovery)

    document["diagnostics_source"] = "home_assistant_download"
    return document
