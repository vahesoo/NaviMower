"""Navimower: private-cloud features with standalone official OAuth/MQTT."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .channel import parse_channels
from .const import (
    API_BASE_URL,
    CONF_API_BASE_URL,
    CONF_AUTH_IMPLEMENTATION,
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

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.LAWN_MOWER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.CAMERA,
    Platform.CALENDAR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# The standalone map card is distributed from its own HACS dashboard repository.
# Mow-now and scheduler remain bundled until their UI is folded into that card.
_CARDS = (
    "navimower-mow-card.js",
    "navimower-scheduler-card.js",
)
_CARD_VERSION = "0.2.1"
_FRONTEND_KEY = f"{DOMAIN}_frontend_registered"


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Copy/register only the still-bundled mow-now and scheduler cards."""
    if hass.data.get(_FRONTEND_KEY):
        return

    here = os.path.dirname(__file__)
    registered = False
    for filename in _CARDS:
        source = os.path.join(here, "www", filename)
        if not os.path.isfile(source):
            _LOGGER.warning("Navimower card asset is missing: %s", source)
            continue

        target_dir = hass.config.path("www", DOMAIN)
        target = os.path.join(target_dir, filename)

        def _copy_asset() -> None:
            os.makedirs(target_dir, exist_ok=True)
            shutil.copyfile(source, target)

        try:
            await hass.async_add_executor_job(_copy_asset)
            from homeassistant.components.frontend import add_extra_js_url

            add_extra_js_url(
                hass,
                f"/local/{DOMAIN}/{filename}?v={_CARD_VERSION}",
            )
            registered = True
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Could not register Navimower card %s",
                filename,
                exc_info=True,
            )

    if registered:
        hass.data[_FRONTEND_KEY] = True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload only when user options change, never on OAuth token refresh.

    Home Assistant writes refreshed OAuth tokens back to ``entry.data``. A
    normal unconditional config-entry listener would therefore unload every
    entity on each token refresh. Keep an explicit options snapshot and ignore
    data-only updates.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    snapshots = domain_data.setdefault("_options_snapshot", {})
    current = dict(entry.options)
    previous = snapshots.get(entry.entry_id)
    if previous == current:
        return
    snapshots[entry.entry_id] = current
    await hass.config_entries.async_reload(entry.entry_id)


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
    await _async_register_frontend(hass)

    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data.setdefault("_options_snapshot", {})[entry.entry_id] = dict(
        entry.options
    )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    coordinator = NavimowCoordinator(hass, entry)
    await coordinator.async_load_persistent_state()
    domain_data[entry.entry_id] = coordinator

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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Quiesce callbacks, unload entities, then close network resources."""
    coordinator: NavimowCoordinator | None = (
        hass.data.get(DOMAIN) or {}
    ).get(entry.entry_id)
    bridge = getattr(coordinator, "mqtt_bridge", None) if coordinator else None

    # Stop watchdog/recovery work and invalidate old callback generations before
    # entities disappear. This prevents a late Paho callback from writing into a
    # coordinator while Home Assistant is reloading the config entry.
    if bridge is not None:
        await bridge.async_quiesce()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        if bridge is not None:
            try:
                await bridge.async_resume()
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "Could not resume Navimower MQTT after a rejected unload",
                    exc_info=True,
                )
        return False

    if coordinator is not None:
        if bridge is not None:
            await bridge.async_stop()
        await coordinator.async_shutdown()
    domain_data = hass.data.get(DOMAIN) or {}
    domain_data.pop(entry.entry_id, None)
    snapshots = domain_data.get("_options_snapshot")
    if isinstance(snapshots, dict):
        snapshots.pop(entry.entry_id, None)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove cached map data and all retained mowing sessions."""
    await NavimowerHistory.async_remove_all(hass, entry.entry_id)
    try:
        await state_store(hass, entry.entry_id).async_remove()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Navimower map-state cleanup failed", exc_info=True)
