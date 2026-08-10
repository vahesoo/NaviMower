"""Extended diagnostics written by the navimower.export_diagnostics action.

Beta23 removes notification/H5 discovery from this action. The action returns to
being a predictable extended on-disk export; H5 frontend discovery now runs only
from Home Assistant's native Download diagnostics flow.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .diagnostics_export import async_build_diagnostics, sanitize


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, default=repr),
        encoding="utf-8",
    )


async def async_export_action_diagnostics(
    hass: HomeAssistant,
    coordinator: Any,
    *,
    include_compressed_map: bool = True,
) -> str:
    """Write extended diagnostics to /config without H5/notification probing."""
    document = await async_build_diagnostics(
        hass,
        coordinator,
        include_compressed_map=include_compressed_map,
    )
    if hasattr(coordinator, "state_transition_diagnostics"):
        document["state_transition_capture"] = sanitize(
            coordinator.state_transition_diagnostics()
        )
    document["diagnostics_source"] = "navimower.export_diagnostics"

    now = datetime.now(timezone.utc)
    folder = Path(hass.config.path("navimower_diagnostics"))
    stamp = now.strftime("%Y%m%d_%H%M%S")
    path = folder / f"navimower_diagnostics_{stamp}.json"
    latest = folder / "navimower_diagnostics_latest.json"
    await hass.async_add_executor_job(_write_json, path, document)
    await hass.async_add_executor_job(_write_json, latest, document)
    return str(path)
