"""Navimower: private-cloud features with standalone official OAuth/MQTT."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .account import shared_private_device_id
from .channel import parse_channels
from .const import (
    API_BASE_URL,
    CONF_API_BASE_URL,
    CONF_AUTH_IMPLEMENTATION,
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_MQTT_SOURCE_ENTRY_ID,
    CONF_OAUTH_TOKEN,
    DEFAULT_INCLUDE_RETURN_TRAIL,
    DEFAULT_TRAIL_RETENTION_DAYS,
    DOMAIN,
    LEGACY_OAUTH_SOURCE_DOMAIN,
    OPT_CHANNELS,
    OPT_GATES,
    OPT_INCLUDE_RETURN_TRAIL,
    OPT_TRAIL_RETENTION_DAYS,
)
from .coordinator import NavimowCoordinator, state_store
from .gate import parse_gates
from .history import NavimowerHistory
from .map_api import async_register_map_api
from .mqtt import NavimowerMqttBridge
from .oauth import async_register_oauth_implementation
from .services import async_setup_services
from .session_archive import SessionArchiveManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.LAWN_MOWER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.TIME,
    Platform.CALENDAR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Development-only options used during the 0.4.1 beta investigation. Stable
# releases use only Home Assistant's native Download diagnostics path.
_DEPRECATED_DIAGNOSTICS_OPTIONS = {"diagnostics_detail", "passive_discovery"}

# The standalone map card, Mow Now dialog and schedule editor are distributed
# from the separate navimower-map-card HACS dashboard repository.


async def _async_private_poll_guard(coordinator: NavimowCoordinator) -> None:
    """Guarantee private-cloud polling even while dense MQTT pushes arrive.

    DataUpdateCoordinator.async_set_updated_data() intentionally restarts the
    coordinator refresh timer. Live mower position can arrive more frequently
    than the private-cloud interval, so the normal timer can otherwise be pushed
    forward forever. This guard only refreshes when the last successful private
    poll is actually due, which also avoids duplicate idle refreshes when the
    normal coordinator schedule is already working.
    """
    try:
        while not coordinator._shutdown_complete:
            interval = (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval is not None
                else 30.0
            )
            interval = max(1.0, float(interval))
            await asyncio.sleep(interval)
            if coordinator._shutdown_complete:
                return
            age = coordinator.private_poll_age()
            if age is not None and age < interval * 0.9:
                continue
            try:
                await coordinator.async_refresh()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Private polling guard refresh failed; normal retry continues",
                    exc_info=True,
                )
    except asyncio.CancelledError:
        raise


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up OAuth, services and authenticated map/history HTTP resources."""
    hass.data.setdefault(DOMAIN, {})
    async_register_oauth_implementation(hass)
    async_register_map_api(hass)
    async_setup_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate v0.1.x source-entry OAuth and JSON options to v0.2.0."""
    if entry.version >= 2:
        return True

    data = dict(entry.data)
    options = dict(entry.options)

    # v0.1.x borrowed OAuth/MQTT from an existing NavimowHA config entry.
    # Copy that token once so Navimower becomes fully standalone.
    source_id = data.pop(CONF_MQTT_SOURCE_ENTRY_ID, None)
    if source_id and not data.get(CONF_OAUTH_TOKEN):
        source = hass.config_entries.async_get_entry(str(source_id))
        if source is not None and source.domain == LEGACY_OAUTH_SOURCE_DOMAIN:
            token = source.data.get(CONF_OAUTH_TOKEN)
            if isinstance(token, dict) and token.get("access_token"):
                data[CONF_OAUTH_TOKEN] = dict(token)
                data[CONF_AUTH_IMPLEMENTATION] = DOMAIN
                data[CONF_API_BASE_URL] = source.data.get(
                    CONF_API_BASE_URL,
                    API_BASE_URL,
                )
                _LOGGER.info(
                    "Copied the existing NavimowHA OAuth token into Navimower"
                )

    if data.get(CONF_OAUTH_TOKEN):
        data.setdefault(CONF_AUTH_IMPLEMENTATION, DOMAIN)
        data.setdefault(CONF_API_BASE_URL, API_BASE_URL)

    # Normalize legacy JSON/string options to structured lists.
    options[OPT_CHANNELS] = [
        channel.as_dict() for channel in parse_channels(options.get(OPT_CHANNELS))
    ]
    options[OPT_GATES] = [
        gate.as_dict() for gate in parse_gates(options.get(OPT_GATES))
    ]
    options.setdefault(OPT_TRAIL_RETENTION_DAYS, DEFAULT_TRAIL_RETENTION_DAYS)
    options.setdefault(OPT_INCLUDE_RETURN_TRAIL, DEFAULT_INCLUDE_RETURN_TRAIL)
    for key in _DEPRECATED_DIAGNOSTICS_OPTIONS:
        options.pop(key, None)

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        version=2,
    )
    _LOGGER.info("Migrated Navimower config entry %s to version 2", entry.entry_id)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Restore local data, then start private cloud and OAuth/MQTT in parallel."""
    async_register_oauth_implementation(hass)

    # Remove beta-only diagnostics options even when the user upgrades without
    # opening the options flow. This also guarantees passive discovery cannot
    # remain enabled from an earlier beta configuration.
    cleaned_options = dict(entry.options)
    removed = [
        key for key in _DEPRECATED_DIAGNOSTICS_OPTIONS if key in cleaned_options
    ]
    for key in removed:
        cleaned_options.pop(key, None)
    if removed:
        hass.config_entries.async_update_entry(entry, options=cleaned_options)
        _LOGGER.info(
            "Removed obsolete Navimower beta diagnostics options: %s",
            ", ".join(sorted(removed)),
        )

    # Entries created before v0.3.4-beta2 may contain different app/device IDs
    # for the same private-cloud account. Converge them before constructing the
    # client so an old pair heals automatically on the next reload or restart.
    canonical_device_id = shared_private_device_id(
        hass.config_entries.async_entries(DOMAIN),
        entry.data.get(CONF_EMAIL),
        entry.data.get(CONF_DEVICE_ID),
    )
    if canonical_device_id and entry.data.get(CONF_DEVICE_ID) != canonical_device_id:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_DEVICE_ID: canonical_device_id},
        )
        _LOGGER.info(
            "Aligned Navimower private-cloud identity for account-shared entry %s",
            entry.entry_id,
        )

    domain_data = hass.data.setdefault(DOMAIN, {})
    coordinator = NavimowCoordinator(hass, entry)
    await coordinator.async_load_persistent_state()
    domain_data[entry.entry_id] = coordinator

    # Prepare immutable completed-session render caches independently from the
    # exact timestamped history. Active sessions remain normal live polylines.
    session_archive = SessionArchiveManager(hass, entry.entry_id, coordinator)
    coordinator.session_archive = session_archive
    session_archive.start()

    bridge = NavimowerMqttBridge(hass, entry, coordinator)
    coordinator.mqtt_bridge = bridge

    # Neither cloud branch should block the other. Cached map/history and live
    # MQTT remain useful while one remote service is temporarily unavailable.
    private_task = hass.async_create_task(
        coordinator.async_refresh(),
        f"Navimower private refresh {entry.entry_id}",
    )
    mqtt_task = hass.async_create_task(
        bridge.async_start(),
        f"Navimower MQTT setup {entry.entry_id}",
    )
    private_result, mqtt_result = await asyncio.gather(
        private_task,
        mqtt_task,
        return_exceptions=True,
    )

    if isinstance(private_result, asyncio.CancelledError):
        raise private_result
    if isinstance(mqtt_result, asyncio.CancelledError):
        raise mqtt_result

    if isinstance(private_result, Exception):
        coordinator.set_private_cloud_connected(False, str(private_result))
        _LOGGER.warning(
            "Navimower private-cloud startup failed; cached/live data remains "
            "available: %s",
            private_result,
        )
    if isinstance(mqtt_result, Exception):
        coordinator.set_oauth_connected(False, str(mqtt_result))
        coordinator.set_mqtt_connected(
            False,
            configured=bridge.configured,
            error=str(mqtt_result),
        )
        _LOGGER.warning(
            "Navimower OAuth/MQTT startup failed; private-cloud data remains "
            "available: %s",
            mqtt_result,
        )
        if not isinstance(mqtt_result, ConfigEntryAuthFailed):
            bridge.schedule_start_retry()

    if not coordinator.data:
        coordinator.async_set_updated_data(coordinator.bootstrap_snapshot())

    coordinator.private_poll_guard_task = hass.async_create_background_task(
        _async_private_poll_guard(coordinator),
        f"Navimower private poll guard {entry.entry_id}",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Quiesce callbacks, unload entities, then close network resources."""
    coordinator: NavimowCoordinator | None = (
        hass.data.get(DOMAIN) or {}
    ).get(entry.entry_id)
    bridge = getattr(coordinator, "mqtt_bridge", None) if coordinator else None
    session_archive = (
        getattr(coordinator, "session_archive", None) if coordinator else None
    )
    private_poll_guard = (
        getattr(coordinator, "private_poll_guard_task", None) if coordinator else None
    )

    if private_poll_guard is not None:
        private_poll_guard.cancel()
        await asyncio.gather(private_poll_guard, return_exceptions=True)
        coordinator.private_poll_guard_task = None

    if bridge is not None:
        await bridge.async_quiesce()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        if coordinator is not None and private_poll_guard is not None:
            coordinator.private_poll_guard_task = hass.async_create_background_task(
                _async_private_poll_guard(coordinator),
                f"Navimower private poll guard {entry.entry_id}",
            )
        if bridge is not None:
            try:
                await bridge.async_resume()
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "Could not resume Navimower MQTT after a rejected unload",
                    exc_info=True,
                )
        return False

    if session_archive is not None:
        await session_archive.async_stop()
    if coordinator is not None:
        if bridge is not None:
            await bridge.async_stop()
        await coordinator.async_shutdown()
    domain_data = hass.data.get(DOMAIN) or {}
    domain_data.pop(entry.entry_id, None)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove cached map data and all retained mowing sessions."""
    await SessionArchiveManager.async_remove_all(hass, entry.entry_id)
    await NavimowerHistory.async_remove_all(hass, entry.entry_id)
    try:
        await state_store(hass, entry.entry_id).async_remove()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Navimower map-state cleanup failed", exc_info=True)
