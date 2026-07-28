"""Navimower: private-cloud map/sensors with official MQTT live position."""
from __future__ import annotations

import logging
import os
import shutil

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import NavimowCoordinator
from .map_api import async_register_map_api
from .mqtt import NavimowerMqttBridge
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

_CARDS = (
    "navimower-scheduler-card.js",
    "navimower-mow-card.js",
    "navimower-map-card.js",
)
_CARD_VER = "0.1.1"
_FRONTEND_KEY = f"{DOMAIN}_frontend_registered"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up shared HTTP resources."""
    hass.data.setdefault(DOMAIN, {})
    async_register_map_api(hass)
    return True


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Copy and register bundled dashboard cards without blocking setup."""
    if hass.data.get(_FRONTEND_KEY):
        return
    here = os.path.dirname(__file__)
    ok_any = False
    for filename in _CARDS:
        src = os.path.join(here, "www", filename)
        if not os.path.isfile(src):
            _LOGGER.warning("Navimower card asset missing: %s", src)
            continue
        try:
            www_dir = hass.config.path("www", DOMAIN)
            dst = os.path.join(www_dir, filename)

            def _copy() -> None:
                os.makedirs(www_dir, exist_ok=True)
                shutil.copyfile(src, dst)

            await hass.async_add_executor_job(_copy)
            url = f"/local/{DOMAIN}/{filename}?v={_CARD_VER}"
            from homeassistant.components.frontend import add_extra_js_url

            add_extra_js_url(hass, url)
            ok_any = True
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Could not register Navimower card %s", filename, exc_info=True)
    if ok_any:
        hass.data[_FRONTEND_KEY] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one mower."""
    await _async_register_frontend(hass)

    coordinator = NavimowCoordinator(hass, entry)
    await coordinator.async_load_trail()
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # MQTT is optional in this alpha: the private-cloud integration remains
    # usable if the selected official OAuth source is missing or temporarily bad.
    bridge = NavimowerMqttBridge(hass, entry, coordinator)
    coordinator.mqtt_bridge = bridge
    try:
        await bridge.async_start()
    except Exception as err:  # noqa: BLE001
        coordinator.set_mqtt_connected(False, configured=True)
        _LOGGER.warning("Navimower MQTT bridge did not start: %s", err)

    async_setup_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload platforms and disconnect official MQTT."""
    coordinator = (hass.data.get(DOMAIN) or {}).get(entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and coordinator is not None:
        bridge = getattr(coordinator, "mqtt_bridge", None)
        if bridge is not None:
            await bridge.async_stop()
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the persisted MQTT/private-cloud mowing trail."""
    from .coordinator import trail_store

    try:
        await trail_store(hass, entry.entry_id).async_remove()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Navimower trail cleanup failed", exc_info=True)
