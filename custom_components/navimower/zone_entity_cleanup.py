"""Remove zone sensor registry rows after authoritative map zone-ID changes.

Navimow assigns new zone IDs when zones are merged/split or otherwise recreated.
Zone history remains valid, but Home Assistant must not retain unavailable sensor
registry rows for zone IDs that a freshly decoded map no longer contains.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from . import sensor as platform


_LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_ORIGINAL_ASYNC_SETUP_ENTRY = platform.async_setup_entry
_ZONE_METRIC_KEYS = tuple(metric.key for metric in platform.ZONE_METRICS)
_ZONE_METRIC_PARSE_ORDER = tuple(
    sorted(_ZONE_METRIC_KEYS, key=len, reverse=True)
)


def _authoritative_zone_ids(coordinator: Any) -> set[int] | None:
    """Return current map zone IDs only when decoded geometry is trustworthy."""
    data = getattr(coordinator, "data", None) or {}
    map_data = data.get("map")
    if not isinstance(map_data, dict):
        return None
    revision = map_data.get("revision") or map_data.get("map_version")
    zones = map_data.get("zones")
    if not revision or not isinstance(zones, list) or not zones:
        return None

    zone_ids: set[int] = set()
    for zone in zones:
        if not isinstance(zone, dict) or zone.get("id") is None:
            continue
        try:
            zone_ids.add(int(zone["id"]))
        except (TypeError, ValueError):
            continue
    return zone_ids or None


def _zone_registry_key(unique_id: str, mower_sn: str) -> tuple[int, str] | None:
    prefix = f"{mower_sn}_zone_"
    if not unique_id.startswith(prefix):
        return None
    tail = unique_id[len(prefix) :]
    # Longer metric names must be checked first because ``mowed_area`` also
    # ends with ``_area``. A failed shorter suffix parse must never hide a later
    # valid metric match.
    for metric in _ZONE_METRIC_PARSE_ORDER:
        suffix = f"_{metric}"
        if not tail.endswith(suffix):
            continue
        raw_zone_id = tail[: -len(suffix)]
        try:
            return int(raw_zone_id), metric
        except (TypeError, ValueError):
            continue
    return None


def _cleanup_stale_zone_registry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Any,
) -> None:
    """Delete only sensor registry rows proven absent from the current map."""
    current_zone_ids = _authoritative_zone_ids(coordinator)
    if current_zone_ids is None:
        return

    registry = er.async_get(hass)
    removed: list[str] = []
    for registry_entry in list(er.async_entries_for_config_entry(registry, entry.entry_id)):
        if registry_entry.domain != "sensor" or registry_entry.platform != DOMAIN:
            continue
        parsed = _zone_registry_key(registry_entry.unique_id, coordinator.sn)
        if parsed is None:
            continue
        zone_id, _metric = parsed
        if zone_id in current_zone_ids:
            continue
        removed.append(registry_entry.entity_id)
        registry.async_remove(registry_entry.entity_id)

    if removed:
        _LOGGER.info(
            "Removed stale Navimower zone sensor registry entries after map change: %s",
            ", ".join(sorted(removed)),
        )


async def _async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # This is the important upgrade path for orphaned rows already left by older
    # versions. It runs before the platform adds the current zone sensors.
    _cleanup_stale_zone_registry(hass, entry, coordinator)

    await _ORIGINAL_ASYNC_SETUP_ENTRY(hass, entry, async_add_entities)

    # Keep future merge/split edits clean as soon as a confirmed new map reaches
    # the coordinator. A missing/empty/unversioned map intentionally does nothing.
    def _map_update_cleanup() -> None:
        _cleanup_stale_zone_registry(hass, entry, coordinator)

    entry.async_on_unload(coordinator.async_add_listener(_map_update_cleanup))


def install_zone_entity_cleanup() -> None:
    """Install conservative stale-zone registry cleanup once."""
    global _INSTALLED
    if _INSTALLED:
        return
    platform.async_setup_entry = _async_setup_entry
    _INSTALLED = True
