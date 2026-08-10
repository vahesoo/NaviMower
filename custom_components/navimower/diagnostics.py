"""Native Home Assistant diagnostics for Navimower."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .diagnostics_export import async_build_diagnostics, sanitize
from .event_probe import probe_event_endpoints


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

    # Native HA diagnostics should remain reasonably sized. The uncompressed map
    # endpoint is still included in full; the compressed copy is redundant here.
    document = await async_build_diagnostics(
        hass,
        coordinator,
        include_compressed_map=False,
    )
    if hasattr(coordinator, "state_transition_diagnostics"):
        document["state_transition_capture"] = sanitize(
            coordinator.state_transition_diagnostics()
        )

    # Beta19: the Navimow phone app exposes a Device notification timeline, but
    # its runtime API endpoint is still unknown. Probe likely *read* endpoints
    # only when diagnostics are explicitly downloaded. This is intentionally not
    # part of normal polling and never calls a setting/control/map-write route.
    document["notification_event_probe"] = await hass.async_add_executor_job(
        probe_event_endpoints,
        coordinator.client,
        coordinator.sn,
        coordinator.vehicle_type,
    )
    return document
