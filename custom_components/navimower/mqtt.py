"""Standalone official OAuth/MQTT bridge for dense live Navimow position.

The Navimower config entry owns its Smart Home OAuth token. MQTT credentials
are fetched only after the OAuth token is valid. A separate pose-stream
watchdog detects the important failure mode where the broker remains connected
but ``realtimeDate/location`` silently stops, then re-subscribes and finally
rebuilds the MQTT client without reloading the Home Assistant config entry.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
import json
import logging
import time
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
    DEFAULT_PASSIVE_DISCOVERY,
    MQTT_BROKER,
    MQTT_DISCONNECT_TIMEOUT_SECONDS,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_POSE_RECOVERY_STALE_SECONDS,
    MQTT_POSE_RESUBSCRIBE_COOLDOWN_SECONDS,
    MQTT_POSE_STALE_SECONDS,
    MQTT_RECOVERY_BACKOFF_SECONDS,
    MQTT_RESUBSCRIBE_GRACE_SECONDS,
    MQTT_USERNAME,
    MQTT_WATCHDOG_INTERVAL_SECONDS,
)
from .location import extract_mqtt_battery, location_topic, parse_location_payload

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
        self._device_id = ""
        self._location_cache: dict[str, dict[str, Any]] = {}
        self._hook_state: dict[int, dict[str, Any]] = {}

        self._refresh_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._recovery_lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()

        self._retry_task: asyncio.Task[Any] | None = None
        self._watchdog_task: asyncio.Task[Any] | None = None
        self._recovery_task: asyncio.Task[Any] | None = None

        self._generation = 0
        self._unloading = False
        self._quiescing = False
        self._stopped = False
        self._reauth_started = False
        self._uses_auth_headers = False

        self._started_mono: float | None = None
        self._connected_mono: float | None = None
        self._subscribed_mono: float | None = None
        self._last_any_message_mono: float | None = None
        self._last_location_message_mono: float | None = None
        self._last_valid_pose_mono: float | None = None
        self._next_recovery_mono = 0.0
        self._recovery_count = 0
        self._recovery_total = 0
        self._recovery_state = "stopped"
        self._last_recovery_reason: str | None = None
        self._last_recovery_utc: str | None = None

        self._message_inventory: dict[str, dict[str, Any]] = {}
        self._discovery_enabled = bool(
            entry.options.get(OPT_PASSIVE_DISCOVERY, DEFAULT_PASSIVE_DISCOVERY)
        )
        self._discovery_inventory: dict[str, dict[str, Any]] = {}
        self._discovery_markers: list[dict[str, Any]] = []
        self._discovery_dropped_topics = 0
        cloud_client = getattr(coordinator, "client", None)
        if hasattr(cloud_client, "set_discovery_enabled"):
            cloud_client.set_discovery_enabled(self._discovery_enabled)

    @property
    def configured(self) -> bool:
        return bool(self.entry.data.get(CONF_OAUTH_TOKEN))

    @property
    def discovery_enabled(self) -> bool:
        """Return whether temporary passive discovery is enabled."""
        return self._discovery_enabled

    # ------------------------------------------------------------- lifecycle
    async def async_start(self) -> bool:
        """Validate OAuth, discover this mower and start official MQTT."""
        async with self._start_lock:
            return await self._async_start_locked()

    async def _async_start_locked(self) -> bool:
        """Start MQTT while the start lock is held."""
        if self._unloading or self._quiescing or self._stopped:
            return False
        if self.sdk is not None:
            self._ensure_watchdog()
            return True
        if not self.configured:
            message = "Navimower Smart Home OAuth is not configured"
            self.coordinator.set_oauth_connected(False, message)
            self.coordinator.set_mqtt_connected(
                False, configured=False, error=message
            )
            await self._async_request_reauth()
            raise ConfigEntryAuthFailed(message)

        self._set_recovery_state("connecting")
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
        self._device_id = official_id
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
        self._generation += 1
        generation = self._generation
        self.sdk = sdk
        self._started_mono = time.monotonic()
        self._connected_mono = None
        self._subscribed_mono = None
        self._last_any_message_mono = None
        self._last_location_message_mono = None
        self._last_valid_pose_mono = None
        try:
            self._attach_hooks(sdk, official_id, generation)
        except Exception:
            self._generation += 1
            self.sdk = None
            await self._async_disconnect_sdk(sdk)
            raise

        self.coordinator.set_oauth_connected(True)
        self.coordinator.set_mqtt_connected(
            bool(self.sdk.is_connected),
            configured=True,
        )
        self._ensure_watchdog()
        _LOGGER.info(
            "Navimower MQTT started for mower %s using official device %s",
            self._masked_serial(self.coordinator.sn),
            official_id or "unknown",
        )
        return True

    async def async_quiesce(self) -> None:
        """Suppress callbacks and recovery before Home Assistant unloads entities."""
        if self._quiescing:
            return
        self._quiescing = True
        self._unloading = True
        self._generation += 1
        if self.sdk is not None:
            self._detach_hooks(self.sdk)
        retry, watchdog, recovery = (
            self._retry_task,
            self._watchdog_task,
            self._recovery_task,
        )
        self._retry_task = None
        self._watchdog_task = None
        self._recovery_task = None
        await self._cancel_tasks(retry, watchdog, recovery)
        self._set_recovery_state("quiesced")
        self.coordinator.set_mqtt_connected(False, configured=self.configured)

    async def async_resume(self) -> None:
        """Recover after a platform unload was rejected by Home Assistant."""
        async with self._stop_lock:
            sdk = self.sdk
            self.sdk = None
            self._generation += 1
            if sdk is not None:
                await self._async_disconnect_sdk(sdk)
            self._stopped = False
            self._quiescing = False
            self._unloading = False
        await self.async_start()

    async def async_stop(self) -> None:
        """Idempotently disconnect MQTT and cancel every bridge-owned task."""
        async with self._stop_lock:
            if self._stopped and self.sdk is None:
                return
            await self.async_quiesce()
            self._stopped = True
            sdk = self.sdk
            self.sdk = None
            self.device = None
            self.api = None
            self.oauth_session = None
            self._generation += 1
            if sdk is not None:
                await self._async_disconnect_sdk(sdk)
            self._set_recovery_state("stopped")

    async def _async_disconnect_sdk(self, sdk: Any) -> None:
        """Detach callbacks and disconnect a possibly wedged SDK with a timeout."""
        # Restore the SDK callbacks before stopping its paho thread. Otherwise a
        # late thread callback can instantiate our async wrapper after Home
        # Assistant's loop is already closing, producing "was never awaited".
        self._detach_hooks(sdk)
        try:
            await asyncio.wait_for(
                self.hass.async_add_executor_job(sdk.disconnect),
                timeout=MQTT_DISCONNECT_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            _LOGGER.warning(
                "Timed out disconnecting the old Navimower MQTT client after %s s",
                MQTT_DISCONNECT_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("MQTT disconnect failed", exc_info=True)

    @staticmethod
    async def _cancel_tasks(*tasks: asyncio.Task[Any] | None) -> None:
        current = asyncio.current_task()
        pending = [task for task in tasks if task is not None and task is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def schedule_start_retry(self) -> None:
        """Retry a transient initial OAuth/API/MQTT failure in the background."""
        if (
            self._unloading
            or self._quiescing
            or self._stopped
            or self._retry_task is not None
            or self.sdk is not None
        ):
            return
        # This retry loop may remain alive through a prolonged outage. It must
        # not extend Home Assistant's integration-startup tracking window.
        self._retry_task = self.hass.async_create_background_task(
            self._async_retry_start(),
            f"Retry Navimower MQTT setup {self.entry.entry_id}",
        )

    async def _async_retry_start(self) -> None:
        delay = 30
        try:
            while not self._unloading and not self._stopped and self.sdk is None:
                await asyncio.sleep(delay)
                try:
                    await self.async_start()
                except ConfigEntryAuthFailed:
                    return
                except Exception as err:  # noqa: BLE001
                    self.coordinator.set_mqtt_connected(
                        False, configured=self.configured, error=str(err)
                    )
                    next_delay = min(delay * 2, 600)
                    _LOGGER.warning(
                        "Navimower MQTT startup retry failed; retrying in %s s: %s",
                        next_delay,
                        err,
                    )
                    delay = next_delay
                else:
                    return
        except asyncio.CancelledError:
            raise
        finally:
            if self._retry_task is asyncio.current_task():
                self._retry_task = None

    # ---------------------------------------------------------- MQTT hooks
    def _attach_hooks(self, sdk: Any, device_id: str, generation: int) -> None:
        mqtt = sdk._mqtt
        originals = {
            "on_connected": getattr(mqtt, "on_connected", None),
            "on_ready": getattr(mqtt, "on_ready", None),
            "on_disconnected": getattr(mqtt, "on_disconnected", None),
            "on_message": getattr(mqtt, "on_message", None),
        }
        original_on_message = originals["on_message"]

        def current() -> bool:
            return (
                not self._unloading
                and not self._quiescing
                and not self._stopped
                and self.sdk is sdk
                and self._generation == generation
            )

        async def _on_connected() -> None:
            if not current():
                return
            self._connected_mono = time.monotonic()
            self.coordinator.set_mqtt_connected(True, configured=True)
            await self._async_subscribe_location(sdk, device_id, generation)

        async def _on_ready() -> None:
            if not current():
                return
            self.coordinator.set_mqtt_connected(True, configured=True)
            await self._async_subscribe_location(sdk, device_id, generation)

        async def _on_disconnected() -> None:
            if not current():
                return
            self._set_recovery_state("disconnected", "broker disconnected")
            self.coordinator.set_mqtt_connected(False, configured=True)
            if self._refresh_lock.locked():
                return
            async with self._refresh_lock:
                if current():
                    await self._async_refresh_credentials(sdk, generation)

        async def _on_message(
            topic: str,
            payload: bytes,
            incoming_device_id: str,
        ) -> None:
            if not current():
                return
            now = time.monotonic()
            self._record_message_inventory(topic, payload, incoming_device_id)
            if incoming_device_id == device_id:
                self._last_any_message_mono = now
            if incoming_device_id == device_id and topic.endswith(
                "/realtimeDate/state"
            ):
                try:
                    parsed_state = json.loads(
                        (payload or b"").decode("utf-8", errors="replace")
                    )
                except (TypeError, ValueError):
                    parsed_state = None
                if isinstance(parsed_state, dict):
                    battery = extract_mqtt_battery(parsed_state)
                    if battery is not None:
                        self.coordinator.ingest_mqtt_state(
                            {
                                "battery": battery,
                                "timestamp": parsed_state.get("timestamp"),
                                "state": parsed_state.get("state")
                                or parsed_state.get("status")
                                or parsed_state.get("vehicleState"),
                            }
                        )
            if incoming_device_id == device_id and topic.endswith(
                "/realtimeDate/location"
            ):
                self._last_location_message_mono = now
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
                    # The parser keeps cached X/Y on progress/state packets.
                    # Only an actual type=1 pose update proves the live stream
                    # recovered; cached coordinates must never reset watchdog age.
                    pose_updated = bool(location.get("_pose_updated"))
                    self.coordinator.ingest_mqtt_location(location)
                    if pose_updated:
                        self._mark_pose_recovered()
                return
            if original_on_message is not None:
                await original_on_message(topic, payload, incoming_device_id)

        mqtt.on_connected = _on_connected
        mqtt.on_ready = _on_ready
        mqtt.on_disconnected = _on_disconnected
        mqtt.on_message = _on_message
        self._hook_state[id(sdk)] = {
            "mqtt": mqtt,
            "originals": originals,
            "hooks": {
                "on_connected": _on_connected,
                "on_ready": _on_ready,
                "on_disconnected": _on_disconnected,
                "on_message": _on_message,
            },
        }
        if sdk.is_connected:
            self.hass.async_create_task(_on_connected())

    def _detach_hooks(self, sdk: Any) -> None:
        """Restore callbacks installed by this bridge without touching newer hooks."""
        state = self._hook_state.pop(id(sdk), None)
        if not isinstance(state, dict):
            return
        mqtt = state.get("mqtt")
        originals = state.get("originals") or {}
        hooks = state.get("hooks") or {}
        if mqtt is None:
            return
        for name, hook in hooks.items():
            try:
                if getattr(mqtt, name, None) is hook:
                    setattr(mqtt, name, originals.get(name))
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Could not restore MQTT hook %s", name, exc_info=True)

    async def _async_subscribe_location(
        self, sdk: Any, device_id: str, generation: int
    ) -> bool:
        if (
            self.sdk is not sdk
            or self._generation != generation
            or self._unloading
            or self._quiescing
        ):
            return False
        try:
            sdk._mqtt.client.subscribe(location_topic(device_id))
        except Exception as err:  # noqa: BLE001
            self._set_recovery_state("subscribe_failed", str(err))
            _LOGGER.warning("Could not subscribe to Navimow location: %s", err)
            return False
        if self._discovery_enabled:
            try:
                sdk._mqtt.client.subscribe(mqtt_discovery_topic(device_id))
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Could not subscribe to Navimower passive discovery: %s", err)
        self._subscribed_mono = time.monotonic()
        self._set_recovery_state("subscribed")
        return True

    # -------------------------------------------------------------- watchdog
    def _ensure_watchdog(self) -> None:
        if (
            self._watchdog_task is not None
            or self._unloading
            or self._quiescing
            or self._stopped
        ):
            return
        # The watchdog is intentionally long-lived. Register it as a background
        # task so Home Assistant does not wait for it during integration startup.
        self._watchdog_task = self.hass.async_create_background_task(
            self._async_watchdog_loop(),
            f"Navimower MQTT watchdog {self.entry.entry_id}",
        )

    async def _async_watchdog_loop(self) -> None:
        try:
            while not self._unloading and not self._stopped:
                await asyncio.sleep(MQTT_WATCHDOG_INTERVAL_SECONDS)
                if self._quiescing:
                    return
                expected = bool(
                    getattr(self.coordinator, "mqtt_stream_expected", lambda: False)()
                )
                if not expected:
                    if self._recovery_task is not None:
                        task = self._recovery_task
                        self._recovery_task = None
                        task.cancel()
                    if self.sdk is not None:
                        self._set_recovery_state("idle")
                    continue

                age = self.coordinator.pose_age()
                if age is not None and age <= MQTT_POSE_RECOVERY_STALE_SECONDS:
                    self._set_recovery_state("live")
                    continue

                anchor = self._connected_mono or self._started_mono
                if age is None and anchor is not None:
                    if time.monotonic() - anchor <= MQTT_POSE_RECOVERY_STALE_SECONDS:
                        continue

                if time.monotonic() < self._next_recovery_mono:
                    self._set_recovery_state("pose_degraded")
                    continue
                if self._recovery_task is None:
                    reason = (
                        "no live pose received"
                        if age is None
                        else f"pose stream stale for {age:.1f} s"
                    )
                    self._recovery_task = self.hass.async_create_task(
                        self._async_recovery_cycle(reason),
                        f"Recover Navimower MQTT pose {self.entry.entry_id}",
                    )
        except asyncio.CancelledError:
            raise
        finally:
            if self._watchdog_task is asyncio.current_task():
                self._watchdog_task = None

    async def _async_recovery_cycle(self, reason: str) -> None:
        """Re-subscribe a degraded pose stream without rebuilding MQTT.

        Field diagnostics show that Navimow can keep the broker connection and
        current-device state traffic alive while type=1 location/pose packets
        disappear for minutes. Rebuilding the whole SDK in that condition can
        create a reconnect storm and does not fix the vendor-side publisher.
        """
        try:
            self._recovery_total += 1
            sdk = self.sdk
            generation = self._generation
            device_id = self._device_id
            self._set_recovery_state("resubscribing", reason)
            _LOGGER.warning(
                "Navimower MQTT %s while mower is active; re-subscribing to location",
                reason,
            )
            if sdk is not None:
                await self._async_subscribe_location(sdk, device_id, generation)
            await asyncio.sleep(MQTT_RESUBSCRIBE_GRACE_SECONDS)
            if self._unloading or self._quiescing or self._stopped:
                return
            if not self.coordinator.mqtt_stream_expected():
                return
            age = self.coordinator.pose_age()
            if age is not None and age <= MQTT_POSE_STALE_SECONDS:
                self._mark_pose_recovered()
                return
            # Transport can be healthy while only pose is missing. Preserve the
            # client and useful MQTT state/progress traffic; position continues
            # through the private-cloud freshness fallback.
            self._set_recovery_state("pose_degraded", reason)
            self._next_recovery_mono = (
                time.monotonic() + MQTT_POSE_RESUBSCRIBE_COOLDOWN_SECONDS
            )
        except asyncio.CancelledError:
            raise
        finally:
            if self._recovery_task is asyncio.current_task():
                self._recovery_task = None

    async def _async_rebuild_client(self, reason: str) -> None:
        """Recreate the SDK in-place; entities and history stay loaded."""
        async with self._recovery_lock:
            if self._unloading or self._quiescing or self._stopped:
                return
            self._recovery_count += 1
            delay = MQTT_RECOVERY_BACKOFF_SECONDS[
                min(self._recovery_count - 1, len(MQTT_RECOVERY_BACKOFF_SECONDS) - 1)
            ]
            if delay:
                self._set_recovery_state("backoff", reason)
                await asyncio.sleep(delay)
                if self._unloading or self._quiescing or self._stopped:
                    return
                if not self.coordinator.mqtt_stream_expected():
                    return
                age = self.coordinator.pose_age()
                if age is not None and age <= MQTT_POSE_STALE_SECONDS:
                    self._mark_pose_recovered()
                    return

            self._set_recovery_state("rebuilding", reason)
            _LOGGER.warning(
                "Navimower MQTT location did not recover; rebuilding client "
                "without reloading the integration (attempt %s)",
                self._recovery_count,
            )
            try:
                async with self._start_lock:
                    old_sdk = self.sdk
                    self.sdk = None
                    self.device = None
                    self.api = None
                    self.oauth_session = None
                    self._generation += 1
                    self.coordinator.set_mqtt_connected(
                        False, configured=self.configured, error=reason
                    )
                    if old_sdk is not None:
                        await self._async_disconnect_sdk(old_sdk)
                    await self._async_start_locked()
            except ConfigEntryAuthFailed as err:
                self._set_recovery_state("auth_failed", str(err))
                await self._async_request_reauth()
                return
            except Exception as err:  # noqa: BLE001
                message = str(err)
                self.coordinator.set_mqtt_connected(
                    False, configured=self.configured, error=message
                )
                self._set_recovery_state("backoff", message)
                next_delay = MQTT_RECOVERY_BACKOFF_SECONDS[
                    min(self._recovery_count, len(MQTT_RECOVERY_BACKOFF_SECONDS) - 1)
                ]
                self._next_recovery_mono = time.monotonic() + next_delay
                _LOGGER.warning(
                    "Navimower MQTT client rebuild failed; private-cloud fallback "
                    "continues and retry is delayed %s s: %s",
                    next_delay,
                    err,
                )
                return
            self._set_recovery_state("waiting_for_pose", reason)
            self._next_recovery_mono = time.monotonic() + MQTT_RESUBSCRIBE_GRACE_SECONDS

    def _mark_pose_recovered(self) -> None:
        previous = self._recovery_state
        self._last_valid_pose_mono = time.monotonic()
        self._next_recovery_mono = 0.0
        self._recovery_count = 0
        self._set_recovery_state("live")
        task = self._recovery_task
        if task is not None and task is not asyncio.current_task():
            self._recovery_task = None
            task.cancel()
        if previous not in {"live", "subscribed", "idle"}:
            _LOGGER.info("Navimower MQTT live pose stream recovered")

    def _set_recovery_state(self, state: str, reason: str | None = None) -> None:
        reason_changed = bool(reason and reason != self._last_recovery_reason)
        changed = state != self._recovery_state or reason_changed
        self._recovery_state = state
        if reason:
            self._last_recovery_reason = reason
        if changed and state in {
            "resubscribing",
            "rebuilding",
            "backoff",
            "auth_failed",
            "live",
        }:
            self._last_recovery_utc = datetime.now(UTC).isoformat()
        if changed and hasattr(self.coordinator, "publish_connectivity"):
            self.coordinator.publish_connectivity()

    # --------------------------------------------------------------- OAuth
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
            raise ConfigEntryAuthFailed(
                "Navimower OAuth reauthentication required"
            ) from err
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

    async def _async_refresh_credentials(self, sdk: Any, generation: int) -> None:
        """Refresh OAuth first, then request credentials bound to that token."""
        if (
            self.oauth_session is None
            or self.api is None
            or self.sdk is not sdk
            or self._generation != generation
        ):
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
                _LOGGER.warning("Navimower MQTT authorization was rejected: %s", err)
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
            sdk.update_mqtt_credentials(
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
            if self.sdk is sdk and sdk.is_connected:
                await self._async_subscribe_location(sdk, self._device_id, generation)

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
            or code
            in {
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
        """Keep account-wide schema inventory plus opt-in current-device samples."""
        safe_topic = str(topic)
        for candidate in {str(incoming_device_id or ""), str(self._device_id or "")}:
            if candidate:
                safe_topic = safe_topic.replace(candidate, "<device>")
        self._record_inventory_item(self._message_inventory, safe_topic, payload, include_samples=False)
        current_device = bool(
            incoming_device_id == self._device_id
            or (self._device_id and f"/vehicle/{self._device_id}/" in str(topic))
        )
        if not self._discovery_enabled or not current_device:
            return
        if safe_topic not in self._discovery_inventory and len(self._discovery_inventory) >= 64:
            self._discovery_dropped_topics += 1
            return
        self._record_inventory_item(self._discovery_inventory, safe_topic, payload, include_samples=True)

    @staticmethod
    def _record_inventory_item(
        store: dict[str, dict[str, Any]],
        safe_topic: str,
        payload: bytes,
        *,
        include_samples: bool,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        item = store.setdefault(
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
        item["max_payload_bytes"] = max(item["max_payload_bytes"], len(payload or b""))
        try:
            parsed = json.loads((payload or b"").decode("utf-8", errors="replace"))
        except (TypeError, ValueError):
            item["parsed_types"].add("non_json")
        else:
            summary = structure_summary(parsed)
            item["parsed_types"].update(summary["parsed_types"])
            item["top_level_keys"].update(summary["top_level_keys"])
            item["key_paths"].update(summary["key_paths"])
            item["observed_type_values"].update(summary["observed_type_values"])
        if include_samples:
            samples = item.setdefault("samples", [])
            sample = sanitize_discovery_payload(payload)
            if not samples or samples[-1].get("payload") != sample:
                samples.append({"seen_utc": now, "payload": sample})
                del samples[:-3]

    def mark_discovery_event(self, name: str) -> dict[str, Any]:
        """Add a timestamp marker for correlating an app action with traffic."""
        label = " ".join(str(name or "marker").split())[:80] or "marker"
        marker = {
            "name": label,
            "created_utc": datetime.now(UTC).isoformat(),
            "mqtt_message_total": sum(int(item.get("count", 0)) for item in self._discovery_inventory.values()),
        }
        self._discovery_markers.append(marker)
        del self._discovery_markers[:-50]
        return deepcopy(marker)

    def diagnostic_discovery(self) -> dict[str, Any]:
        """Return current-device-only passive discovery data."""
        topics: dict[str, Any] = {}
        for topic, item in deepcopy(self._discovery_inventory).items():
            topics[topic] = {
                **item,
                "parsed_types": sorted(item["parsed_types"]),
                "top_level_keys": sorted(item["top_level_keys"]),
                "key_paths": sorted(item["key_paths"]),
                "observed_type_values": sorted(item["observed_type_values"]),
            }
        wildcard = mqtt_discovery_topic(self._device_id) if self._device_id else None
        if wildcard and self._device_id:
            wildcard = wildcard.replace(self._device_id, "<device>")
        return {
            "enabled": self._discovery_enabled,
            "scope": "current_device_only",
            "wildcard_topic": wildcard,
            "topic_limit": 64,
            "sample_limit_per_topic": 3,
            "marker_limit": 50,
            "dropped_topic_messages": self._discovery_dropped_topics,
            "markers": deepcopy(self._discovery_markers),
            "topics": topics,
        }

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

    def diagnostic_health(self) -> dict[str, Any]:
        """Return value-free bridge lifecycle and recovery diagnostics."""
        now = time.monotonic()

        def age(value: float | None) -> float | None:
            return round(max(0.0, now - value), 1) if value is not None else None

        return {
            "generation": self._generation,
            "sdk_present": self.sdk is not None,
            "sdk_connected": bool(self.sdk and self.sdk.is_connected),
            "stream_expected": bool(
                getattr(self.coordinator, "mqtt_stream_expected", lambda: False)()
            ),
            "stream_state": self._recovery_state,
            "recovery_count": self._recovery_total,
            "pose_resubscribe_count": self._recovery_total,
            "consecutive_rebuild_count": self._recovery_count,
            "last_any_message_scope": "current_device",
            "passive_discovery_enabled": self._discovery_enabled,
            "last_recovery_reason": self._last_recovery_reason,
            "last_recovery_utc": self._last_recovery_utc,
            "started_age_s": age(self._started_mono),
            "connected_age_s": age(self._connected_mono),
            "subscribed_age_s": age(self._subscribed_mono),
            "last_any_message_age_s": age(self._last_any_message_mono),
            "last_location_message_age_s": age(self._last_location_message_mono),
            "last_valid_pose_age_s": age(self._last_valid_pose_mono),
            "watchdog_running": self._watchdog_task is not None,
            "recovery_running": self._recovery_task is not None,
            "quiescing": self._quiescing,
            "stopped": self._stopped,
        }

    @staticmethod
    def _masked_serial(value: str) -> str:
        if len(value) < 8:
            return "***"
        return f"{value[:3]}***{value[-4:]}"
