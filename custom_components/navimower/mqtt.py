"""Standalone official OAuth/MQTT bridge for dense live Navimow position.

The Navimower config entry owns its Smart Home OAuth token. MQTT credentials
are fetched only after the OAuth token is valid and are refreshed again after a
broker disconnect because Navimow binds them to the OAuth session.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
import json
import logging
from typing import Any
from urllib.parse import urlparse

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    OAuth2TokenRequestReauthError,
)
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_BASE_URL,
    CONF_API_BASE_URL,
    CONF_OAUTH_DEVICE_ID,
    CONF_OAUTH_TOKEN,
    MQTT_BROKER,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_USERNAME,
)
from .location import location_topic, parse_location_payload

_LOGGER = logging.getLogger(__name__)


class NavimowerMqttBridge:
    """Connect one private-cloud mower coordinator to official MQTT pose data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.oauth_session: config_entry_oauth2_flow.OAuth2Session | None = None
        self.api: Any = None
        self.sdk: Any = None
        self.device: Any = None
        self._location_cache: dict[str, dict[str, Any]] = {}
        self._refresh_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._retry_task: asyncio.Task[Any] | None = None
        self._unloading = False
        self._reauth_started = False
        self._uses_auth_headers = False
        self._message_inventory: dict[str, dict[str, Any]] = {}

    @property
    def configured(self) -> bool:
        return bool(self.entry.data.get(CONF_OAUTH_TOKEN))

    async def async_start(self) -> bool:
        """Validate OAuth, discover this mower and start official MQTT."""
        async with self._start_lock:
            return await self._async_start_locked()

    async def _async_start_locked(self) -> bool:
        """Start MQTT while the start lock is held."""
        if self._unloading:
            return False
        if self.sdk is not None:
            return True
        if not self.configured:
            message = "Navimower Smart Home OAuth is not configured"
            self.coordinator.set_oauth_connected(False, message)
            self.coordinator.set_mqtt_connected(
                False, configured=False, error=message
            )
            await self._async_request_reauth()
            raise ConfigEntryAuthFailed(message)

        try:
            implementation = (
                await config_entry_oauth2_flow.async_get_config_entry_implementation(
                    self.hass,
                    self.entry,
                )
            )
        except Exception as err:  # noqa: BLE001
            self.coordinator.set_oauth_connected(False, str(err))
            raise ConfigEntryNotReady(
                f"Navimower OAuth implementation is unavailable: {err}"
            ) from err

        self.oauth_session = config_entry_oauth2_flow.OAuth2Session(
            self.hass,
            self.entry,
            implementation,
        )
        try:
            access_token = await self._async_access_token()
        except ConfigEntryAuthFailed as err:
            self.coordinator.set_oauth_connected(False, str(err))
            await self._async_request_reauth()
            raise
        self._reauth_started = False
        self.coordinator.set_oauth_connected(True)

        from mower_sdk.api import MowerAPI

        self.api = MowerAPI(
            session=async_get_clientsession(self.hass),
            token=access_token,
            base_url=self.entry.data.get(CONF_API_BASE_URL, API_BASE_URL),
        )
        try:
            devices = await self.api.async_get_devices()
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:  # noqa: BLE001
            self.coordinator.set_oauth_connected(False, str(err))
            if self._is_auth_error(err):
                await self._async_request_reauth()
                raise ConfigEntryAuthFailed(
                    f"Official Navimow authorization was rejected: {err}"
                ) from err
            raise ConfigEntryNotReady(
                f"Official Navimow device discovery failed: {err}"
            ) from err

        self.device = self._match_device(devices)
        if self.device is None:
            raise ConfigEntryNotReady(
                "Could not match the private-cloud mower to an official OAuth device"
            )
        official_id = str(getattr(self.device, "id", "") or "")
        if official_id and self.entry.data.get(CONF_OAUTH_DEVICE_ID) != official_id:
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, CONF_OAUTH_DEVICE_ID: official_id},
            )

        try:
            mqtt_info = await self.api.async_get_mqtt_user_info()
        except Exception as err:  # noqa: BLE001
            self.coordinator.set_mqtt_connected(
                False, configured=True, error=str(err)
            )
            if self._is_auth_error(err):
                self.coordinator.set_oauth_connected(False, str(err))
                await self._async_request_reauth()
                raise ConfigEntryAuthFailed(
                    f"Official Navimow authorization was rejected: {err}"
                ) from err
            raise ConfigEntryNotReady(
                f"Could not obtain Navimow MQTT credentials: {err}"
            ) from err

        connection = self._connection_details(mqtt_info, access_token)
        from mower_sdk.sdk import NavimowSDK

        def _create_sdk() -> Any:
            sdk = NavimowSDK(
                broker=connection["broker"],
                port=connection["port"],
                username=connection["username"],
                password=connection["password"],
                ws_path=connection["ws_path"],
                auth_headers=connection["auth_headers"],
                loop=self.hass.loop,
                records=devices,
                keepalive_seconds=2400,
                reconnect_min_delay=1,
                reconnect_max_delay=60,
            )
            sdk.connect()
            return sdk

        sdk = await self.hass.async_add_executor_job(_create_sdk)
        self.sdk = sdk
        try:
            self._attach_hooks()
        except Exception:
            self.sdk = None
            try:
                await self.hass.async_add_executor_job(sdk.disconnect)
            except Exception:  # noqa: BLE001
                pass
            raise
        self.coordinator.set_oauth_connected(True)
        self.coordinator.set_mqtt_connected(
            bool(self.sdk.is_connected),
            configured=True,
        )
        _LOGGER.info(
            "Navimower MQTT started for mower %s using official device %s",
            self._masked_serial(self.coordinator.sn),
            official_id or "unknown",
        )
        return True

    def _connection_details(
        self,
        mqtt_info: dict[str, Any],
        access_token: str,
    ) -> dict[str, Any]:
        mqtt_host = mqtt_info.get("mqttHost") or MQTT_BROKER
        mqtt_url = mqtt_info.get("mqttUrl")
        username = mqtt_info.get("userName") or MQTT_USERNAME
        password = mqtt_info.get("pwdInfo") or MQTT_PASSWORD
        port = 443 if mqtt_url else MQTT_PORT
        ws_path = mqtt_url
        if mqtt_url:
            parsed = urlparse(str(mqtt_url))
            if parsed.scheme in ("ws", "wss") and parsed.hostname:
                mqtt_host = parsed.hostname
                port = parsed.port or port
                ws_path = parsed.path or "/"
                if parsed.query:
                    ws_path = f"{ws_path}?{parsed.query}"
        self._uses_auth_headers = bool(ws_path)
        return {
            "broker": mqtt_host,
            "port": port,
            "username": username,
            "password": password,
            "ws_path": ws_path,
            "auth_headers": (
                {"Authorization": f"Bearer {access_token}"}
                if self._uses_auth_headers
                else None
            ),
        }

    async def async_stop(self) -> None:
        """Disconnect MQTT, cancel startup retries and suppress callbacks."""
        self._unloading = True
        retry = self._retry_task
        self._retry_task = None
        if retry is not None:
            retry.cancel()
            try:
                await retry
            except asyncio.CancelledError:
                pass
        self.coordinator.set_mqtt_connected(
            False, configured=self.configured
        )
        sdk = self.sdk
        self.sdk = None
        if sdk is not None:
            try:
                await self.hass.async_add_executor_job(sdk.disconnect)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("MQTT disconnect failed during unload", exc_info=True)

    def schedule_start_retry(self) -> None:
        """Retry a transient initial OAuth/API/MQTT failure in the background."""
        if self._unloading or self._retry_task is not None or self.sdk is not None:
            return
        self._retry_task = self.hass.async_create_task(
            self._async_retry_start(),
            f"Retry Navimower MQTT setup {self.entry.entry_id}",
        )

    async def _async_retry_start(self) -> None:
        delay = 60
        try:
            while not self._unloading and self.sdk is None:
                await asyncio.sleep(delay)
                try:
                    await self.async_start()
                except ConfigEntryAuthFailed:
                    return
                except Exception as err:  # noqa: BLE001
                    self.coordinator.set_mqtt_connected(
                        False, configured=self.configured, error=str(err)
                    )
                    _LOGGER.warning(
                        "Navimower MQTT startup retry failed; retrying in %s s: %s",
                        min(delay * 2, 900),
                        err,
                    )
                    delay = min(delay * 2, 900)
                else:
                    return
        except asyncio.CancelledError:
            raise
        finally:
            self._retry_task = None

    async def _async_access_token(self) -> str:
        if self.oauth_session is None:
            raise ConfigEntryAuthFailed("No Navimower OAuth session")
        try:
            if hasattr(self.oauth_session, "async_ensure_token_valid"):
                await self.oauth_session.async_ensure_token_valid()
                token = self.oauth_session.token
            elif hasattr(self.oauth_session, "async_get_valid_token"):
                token = await self.oauth_session.async_get_valid_token()
            else:
                token = self.oauth_session.token
        except OAuth2TokenRequestReauthError as err:
            raise ConfigEntryAuthFailed("Navimower OAuth reauthentication required") from err
        except ConfigEntryAuthFailed:
            raise
        access_token = token.get("access_token") if token else None
        if not access_token:
            raise ConfigEntryAuthFailed("Navimower OAuth access token is unavailable")
        return str(access_token)

    def _match_device(self, devices: list[Any]) -> Any | None:
        stored_id = str(self.entry.data.get(CONF_OAUTH_DEVICE_ID, "") or "").upper()
        serial = str(self.coordinator.sn).upper()
        for device in devices:
            candidates = {
                str(getattr(device, attr, "") or "").upper()
                for attr in ("serial_number", "serial", "id")
            }
            if stored_id and stored_id in candidates:
                return device
            if serial in candidates:
                return device
        return devices[0] if len(devices) == 1 else None

    def _attach_hooks(self) -> None:
        mqtt = self.sdk._mqtt
        original_on_message = mqtt.on_message
        device_id = str(getattr(self.device, "id", "") or "")

        async def _on_connected() -> None:
            if self._unloading:
                return
            try:
                mqtt.client.subscribe(location_topic(device_id))
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Could not subscribe to Navimow location: %s", err)
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

        async def _on_message(
            topic: str,
            payload: bytes,
            incoming_device_id: str,
        ) -> None:
            self._record_message_inventory(topic, payload, incoming_device_id)
            if incoming_device_id == device_id and topic.endswith(
                "/realtimeDate/location"
            ):
                try:
                    parsed = json.loads(
                        (payload or b"").decode("utf-8", errors="replace")
                    )
                except (TypeError, ValueError):
                    parsed = None
                location = parse_location_payload(
                    self._location_cache,
                    incoming_device_id,
                    parsed,
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
        """Refresh OAuth first, then request credentials bound to that token."""
        if self.oauth_session is None or self.api is None or self.sdk is None:
            return
        try:
            access_token = await self._async_access_token()
            self.coordinator.set_oauth_connected(True)
            self.api.set_token(access_token)
            mqtt_info = await self.api.async_get_mqtt_user_info()
        except ConfigEntryAuthFailed as err:
            message = str(err)
            self.coordinator.set_oauth_connected(False, message)
            self.coordinator.set_mqtt_connected(
                False, configured=True, error=message
            )
            _LOGGER.warning("Navimower MQTT OAuth reauthentication required: %s", err)
            await self._async_request_reauth()
            return
        except Exception as err:  # noqa: BLE001
            message = str(err)
            self.coordinator.set_mqtt_connected(
                False, configured=True, error=message
            )
            if self._is_auth_error(err):
                self.coordinator.set_oauth_connected(False, message)
                _LOGGER.warning(
                    "Navimower MQTT authorization was rejected: %s", err
                )
                await self._async_request_reauth()
                return
            _LOGGER.warning("Navimower MQTT credential refresh failed: %s", err)
            return

        username = mqtt_info.get("userName")
        password = mqtt_info.get("pwdInfo")
        auth_headers = (
            {"Authorization": f"Bearer {access_token}"}
            if self._uses_auth_headers
            else None
        )

        def _update() -> None:
            self.sdk.update_mqtt_credentials(
                auth_headers=auth_headers,
                username=username,
                password=password,
            )

        try:
            await self.hass.async_add_executor_job(_update)
        except Exception as err:  # noqa: BLE001
            self.coordinator.set_mqtt_connected(
                False, configured=True, error=str(err)
            )
            _LOGGER.warning("Navimower MQTT client credential update failed: %s", err)
        else:
            self._reauth_started = False
            self.coordinator.set_oauth_connected(True)

    async def async_request_reauth(self) -> None:
        """Start the OAuth reauthentication flow once."""
        await self._async_request_reauth()

    async def _async_request_reauth(self) -> None:
        if self._reauth_started or self._unloading:
            return
        self._reauth_started = True
        try:
            self.entry.async_start_reauth(
                self.hass, data={"reauth_type": "oauth"}
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not start Navimower reauth flow", exc_info=True)

    @staticmethod
    def _is_auth_error(err: Exception) -> bool:
        """Return whether an SDK/API error clearly represents rejected OAuth."""
        status = getattr(err, "status_code", None)
        code = str(getattr(err, "error_code", "") or "").upper()
        message = str(err).lower()
        return (
            status in {401, 403}
            or code in {
                "AUTH_FAILED",
                "TOKEN_EXPIRED",
                "TOKEN_REFRESH_FAILED",
                "UNAUTHORIZED",
                "FORBIDDEN",
            }
            or any(
                marker in message
                for marker in (
                    "code_oauth_info_illegal",
                    "token expired",
                    "token has expired",
                    "invalid token",
                    "unauthorized",
                    "forbidden",
                )
            )
        )

    # ---------------------------------------------------------- diagnostics
    def _record_message_inventory(
        self,
        topic: str,
        payload: bytes,
        incoming_device_id: str,
    ) -> None:
        """Keep a value-free inventory of MQTT topics and JSON key paths."""
        safe_topic = str(topic)
        if incoming_device_id:
            safe_topic = safe_topic.replace(str(incoming_device_id), "<device>")
        now = datetime.now(UTC).isoformat()
        item = self._message_inventory.setdefault(
            safe_topic,
            {
                "count": 0,
                "first_seen_utc": now,
                "last_seen_utc": now,
                "max_payload_bytes": 0,
                "parsed_types": set(),
                "top_level_keys": set(),
                "key_paths": set(),
                "observed_type_values": set(),
            },
        )
        item["count"] += 1
        item["last_seen_utc"] = now
        item["max_payload_bytes"] = max(
            item["max_payload_bytes"],
            len(payload or b""),
        )
        try:
            parsed = json.loads((payload or b"").decode("utf-8", errors="replace"))
        except (TypeError, ValueError):
            item["parsed_types"].add("non_json")
            return

        def walk(value: Any, path: str = "") -> None:
            item["parsed_types"].add(type(value).__name__)
            if isinstance(value, dict):
                for key, child in value.items():
                    child_path = f"{path}.{key}" if path else str(key)
                    item["key_paths"].add(child_path)
                    if not path:
                        item["top_level_keys"].add(str(key))
                    if str(key) in {"type", "action", "vehicleState", "eventCode"}:
                        if isinstance(child, (str, int, float, bool)):
                            item["observed_type_values"].add(f"{key}={child}")
                    walk(child, child_path)
            elif isinstance(value, list):
                for child in value[:25]:
                    walk(child, f"{path}[]" if path else "[]")

        walk(parsed)

    def diagnostic_inventory(self) -> dict[str, Any]:
        """Return the passive MQTT topic/key inventory as JSON-safe data."""
        out: dict[str, Any] = {}
        for topic, item in deepcopy(self._message_inventory).items():
            out[topic] = {
                **item,
                "parsed_types": sorted(item["parsed_types"]),
                "top_level_keys": sorted(item["top_level_keys"]),
                "key_paths": sorted(item["key_paths"]),
                "observed_type_values": sorted(item["observed_type_values"]),
            }
        return out

    @staticmethod
    def _masked_serial(value: str) -> str:
        if len(value) < 8:
            return "***"
        return f"{value[:3]}***{value[-4:]}"
