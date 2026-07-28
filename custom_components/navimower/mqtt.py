"""Official OAuth/MQTT bridge for dense live Navimow position updates.

The alpha release intentionally reuses an existing ``navimow`` config entry as
its OAuth source. Private-cloud authentication remains separate and is handled
by the main Navimower config entry.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_BASE_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    CONF_MQTT_SOURCE_ENTRY_ID,
    MQTT_BROKER,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_USERNAME,
    OAUTH_SOURCE_DOMAIN,
)
from .location import location_topic, parse_location_payload
from .oauth import NavimowOAuth2Implementation

_LOGGER = logging.getLogger(__name__)


class NavimowerMqttBridge:
    """Connect one private-cloud mower coordinator to official MQTT pose data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.source_entry: ConfigEntry | None = None
        self.oauth_session: config_entry_oauth2_flow.OAuth2Session | None = None
        self.api: Any = None
        self.sdk: Any = None
        self.device: Any = None
        self._location_cache: dict[str, dict[str, Any]] = {}
        self._refresh_lock = asyncio.Lock()
        self._unloading = False

    async def async_start(self) -> bool:
        """Start the bridge. Return False when no OAuth source was configured."""
        source_id = self.entry.data.get(CONF_MQTT_SOURCE_ENTRY_ID)
        if not source_id:
            _LOGGER.info("Navimower MQTT bridge disabled: no OAuth source entry selected")
            self.coordinator.set_mqtt_connected(False, configured=False)
            return False

        source_entry = self.hass.config_entries.async_get_entry(str(source_id))
        if source_entry is None or source_entry.domain != OAUTH_SOURCE_DOMAIN:
            raise ConfigEntryNotReady(
                "The selected official Navimow OAuth config entry no longer exists"
            )
        self.source_entry = source_entry

        # Use the official Navimow OAuth implementation against the existing
        # source config entry. The source entry owns the token and receives any
        # refreshed token; Navimower does not duplicate OAuth credentials.
        implementation = NavimowOAuth2Implementation(
            self.hass, OAUTH_SOURCE_DOMAIN, CLIENT_ID, CLIENT_SECRET
        )
        self.oauth_session = config_entry_oauth2_flow.OAuth2Session(
            self.hass, source_entry, implementation
        )
        access_token = await self._async_access_token()

        from mower_sdk.api import MowerAPI

        self.api = MowerAPI(
            session=async_get_clientsession(self.hass),
            token=access_token,
            base_url=source_entry.data.get("api_base_url", API_BASE_URL),
        )
        try:
            devices = await self.api.async_get_devices()
        except Exception as err:
            raise ConfigEntryNotReady(f"Official Navimow device discovery failed: {err}") from err

        self.device = self._match_device(devices)
        if self.device is None:
            raise ConfigEntryNotReady(
                "Could not match the private-cloud mower serial to an official OAuth device"
            )

        try:
            mqtt_info = await self.api.async_get_mqtt_user_info()
        except Exception as err:
            raise ConfigEntryNotReady(f"Could not obtain Navimow MQTT credentials: {err}") from err

        mqtt_host = mqtt_info.get("mqttHost") or source_entry.data.get(
            "mqtt_broker", MQTT_BROKER
        )
        mqtt_url = mqtt_info.get("mqttUrl")
        mqtt_username = mqtt_info.get("userName") or source_entry.data.get(
            "mqtt_username", MQTT_USERNAME
        )
        mqtt_password = mqtt_info.get("pwdInfo") or source_entry.data.get(
            "mqtt_password", MQTT_PASSWORD
        )
        mqtt_port = 443 if mqtt_url else source_entry.data.get("mqtt_port", MQTT_PORT)
        ws_path = mqtt_url
        if mqtt_url:
            parsed = urlparse(mqtt_url)
            if parsed.scheme in ("ws", "wss") and parsed.hostname:
                mqtt_host = parsed.hostname or mqtt_host
                mqtt_port = parsed.port or mqtt_port
                ws_path = parsed.path or "/"
                if parsed.query:
                    ws_path = f"{ws_path}?{parsed.query}"
        auth_headers = {"Authorization": f"Bearer {access_token}"} if ws_path else None

        from mower_sdk.sdk import NavimowSDK

        def _create_sdk() -> Any:
            sdk = NavimowSDK(
                broker=mqtt_host,
                port=mqtt_port,
                username=mqtt_username,
                password=mqtt_password,
                ws_path=ws_path,
                auth_headers=auth_headers,
                loop=self.hass.loop,
                records=devices,
                keepalive_seconds=2400,
                reconnect_min_delay=1,
                reconnect_max_delay=60,
            )
            sdk.connect()
            return sdk

        self.sdk = await self.hass.async_add_executor_job(_create_sdk)
        self._attach_hooks()
        self.coordinator.set_mqtt_connected(bool(self.sdk.is_connected), configured=True)
        _LOGGER.info(
            "Navimower MQTT bridge started for private SN=%s official device=%s",
            self._masked_serial(self.coordinator.sn),
            getattr(self.device, "id", "unknown"),
        )
        return True

    async def async_stop(self) -> None:
        """Disconnect MQTT and suppress reconnect credential work."""
        self._unloading = True
        self.coordinator.set_mqtt_connected(False, configured=bool(self.source_entry))
        if self.sdk is not None:
            try:
                await self.hass.async_add_executor_job(self.sdk.disconnect)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("MQTT disconnect failed during unload", exc_info=True)

    async def _async_access_token(self) -> str:
        if self.oauth_session is None:
            raise ConfigEntryAuthFailed("No OAuth session")
        token: dict[str, Any] | None = None
        if hasattr(self.oauth_session, "async_ensure_token_valid"):
            await self.oauth_session.async_ensure_token_valid()
            token = self.oauth_session.token
        elif hasattr(self.oauth_session, "async_get_valid_token"):
            token = await self.oauth_session.async_get_valid_token()
        else:
            token = self.oauth_session.token
        access_token = token.get("access_token") if token else None
        if not access_token:
            raise ConfigEntryAuthFailed("Official Navimow OAuth token is unavailable")
        return str(access_token)

    def _match_device(self, devices: list[Any]) -> Any | None:
        sn = str(self.coordinator.sn).upper()
        for device in devices:
            candidates = {
                str(getattr(device, "serial_number", "") or "").upper(),
                str(getattr(device, "serial", "") or "").upper(),
                str(getattr(device, "id", "") or "").upper(),
            }
            if sn in candidates:
                return device
        return devices[0] if len(devices) == 1 else None

    def _attach_hooks(self) -> None:
        mqtt = self.sdk._mqtt
        original_on_message = mqtt.on_message
        device_id = str(getattr(self.device, "id", ""))

        async def _on_connected() -> None:
            if self._unloading:
                return
            try:
                mqtt.client.subscribe(location_topic(device_id))
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Could not subscribe to Navimow location topic: %s", err)
            self.coordinator.set_mqtt_connected(True, configured=True)

        async def _on_ready() -> None:
            self.coordinator.set_mqtt_connected(True, configured=True)

        async def _on_disconnected() -> None:
            self.coordinator.set_mqtt_connected(False, configured=True)
            if self._unloading or self._refresh_lock.locked():
                return
            async with self._refresh_lock:
                if not self._unloading:
                    await self._async_refresh_credentials()

        async def _on_message(topic: str, payload: bytes, incoming_device_id: str) -> None:
            if incoming_device_id == device_id and topic.endswith("/realtimeDate/location"):
                try:
                    parsed = json.loads((payload or b"").decode("utf-8", errors="replace"))
                except (TypeError, ValueError):
                    parsed = None
                location = parse_location_payload(
                    self._location_cache, incoming_device_id, parsed
                )
                if location is not None:
                    self.coordinator.ingest_mqtt_location(location)
                return
            if original_on_message is not None:
                await original_on_message(topic, payload, incoming_device_id)

        mqtt.on_connected = _on_connected
        mqtt.on_ready = _on_ready
        mqtt.on_disconnected = _on_disconnected
        mqtt.on_message = _on_message
        if self.sdk.is_connected:
            self.hass.async_create_task(_on_connected())

    async def _async_refresh_credentials(self) -> None:
        """Refresh OAuth and MQTT credentials after a broker disconnect."""
        if self.oauth_session is None or self.api is None or self.sdk is None:
            return
        try:
            access_token = await self._async_access_token()
            self.api.set_token(access_token)
            mqtt_info = await self.api.async_get_mqtt_user_info()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Navimower MQTT credential refresh failed: %s", err)
            return

        username = mqtt_info.get("userName")
        password = mqtt_info.get("pwdInfo")
        auth_headers = {"Authorization": f"Bearer {access_token}"}

        def _update() -> None:
            self.sdk.update_mqtt_credentials(
                auth_headers=auth_headers,
                username=username,
                password=password,
            )

        try:
            await self.hass.async_add_executor_job(_update)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Navimower MQTT client credential update failed: %s", err)

    @staticmethod
    def _masked_serial(value: str) -> str:
        if len(value) < 8:
            return "***"
        return f"{value[:3]}***{value[-4:]}"
