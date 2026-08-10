"""Native Home Assistant diagnostics for Navimower."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .diagnostics_export import async_build_diagnostics, sanitize
from .h5_discovery import probe_h5_frontend


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

    # Beta23 moves notification discovery back to the native Download diagnostics
    # flow, but no longer brute-forces API paths. It follows only public H5 HTML
    # and a bounded set of referenced JavaScript assets, storing structural clues.
    document["h5_frontend_discovery"] = await hass.async_add_executor_job(
        probe_h5_frontend,
        coordinator.client,
    )
    document["diagnostics_source"] = "home_assistant_download"
    return document
