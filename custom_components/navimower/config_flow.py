"""Configuration and options flows for the standalone Navimower integration.

A Navimower entry contains two independent sessions:

* private app-cloud credentials for maps, settings, schedules and commands;
* Smart Home OAuth credentials for official MQTT live position.

The account password is only used during the private login step and is never
stored. The OAuth token is managed by Home Assistant's OAuth helper.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .account import shared_private_device_id
from .api import NavimowCloudClient, NavimowError, PassportAuthError, PassportError
from .channel import NavimowerChannel, parse_channels
from .const import (
    API_BASE_URL,
    CONF_ACCESS_TOKEN,
    CONF_API_BASE_URL,
    CONF_DEVICE_ID,
    CONF_LANGUAGE,
    CONF_MODEL,
    CONF_OAUTH_DEVICE_ID,
    CONF_PASSPORT_UUID,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    CONF_UID,
    CONF_VEHICLE_NAME,
    CONF_VEHICLE_SN,
    CONF_VEHICLE_TYPE,
    DEFAULT_DIAGNOSTICS_DETAIL,
    DEFAULT_INCLUDE_RETURN_TRAIL,
    DEFAULT_LANGUAGE,
    DEFAULT_TRAIL_RETENTION_DAYS,
    DOMAIN,
    OPT_CHANNELS,
    OPT_DIAGNOSTICS_DETAIL,
    OPT_GATES,
    OPT_INCLUDE_RETURN_TRAIL,
    OPT_TRAIL_RETENTION_DAYS,
    OPT_ZONES,
    TRAIL_RETENTION_OPTIONS,
)
from .gate import NavimowerGate, parse_gates
from .oauth import async_register_oauth_implementation

_LOGGER = logging.getLogger(__name__)
_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD,
                autocomplete="current-password",
            )
        ),
    }
)


def _device_candidates(device: Any) -> set[str]:
    return {
        str(getattr(device, attr, "") or "").upper()
        for attr in ("serial_number", "serial", "id")
    }


class NavimowConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    """Set up private app-cloud authentication followed by official OAuth."""

    DOMAIN = DOMAIN
    VERSION = 2

    def __init__(self) -> None:
        super().__init__()
        self._email: str | None = None
        self._device_id: str | None = None
        self._client: NavimowCloudClient | None = None
        self._vehicles: list[dict[str, Any]] = []
        self._pending_data: dict[str, Any] | None = None
        self._reauth_mode: str | None = None

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    def _linked_entry(self) -> ConfigEntry | None:
        """Return the reauth/reconfigure entry linked to this flow."""
        if self.source == SOURCE_REAUTH:
            return self._get_reauth_entry()
        if self.source == SOURCE_RECONFIGURE:
            return self._get_reconfigure_entry()
        return None

    # ------------------------------------------------------------- private auth
    async def _authenticate(self, email: str, password: str) -> list[dict[str, Any]]:
        # The private cloud binds one app/device identity to an account. Reuse
        # the identity already stored by another mower entry of this account so
        # logging in one mower cannot invalidate the other mower's session.
        device_id = (
            shared_private_device_id(
                self._async_current_entries(),
                email,
                self._device_id,
            )
            or uuid.uuid4().hex
        )
        self._device_id = device_id
        client = NavimowCloudClient(device_id=device_id, language=DEFAULT_LANGUAGE)

        def _do() -> list[dict[str, Any]]:
            client.authenticate(email, password)
            client.mower_login()
            return client.auth_list()

        vehicles = await self.hass.async_add_executor_job(_do)
        self._client = client
        return vehicles

    def _private_entry_data(self, vehicle: dict[str, Any]) -> dict[str, Any]:
        assert self._client is not None
        state = self._client.session_state()
        return {
            CONF_EMAIL: self._email,
            CONF_ACCESS_TOKEN: state["access_token"],
            CONF_REFRESH_TOKEN: state["refresh_token"],
            CONF_PASSPORT_UUID: state["uuid"],
            CONF_UID: state["uid"],
            CONF_DEVICE_ID: self._client.device_id,
            CONF_REGION: state["region"],
            CONF_LANGUAGE: DEFAULT_LANGUAGE,
            CONF_VEHICLE_SN: str(vehicle.get("vehicle_sn", "")),
            CONF_VEHICLE_TYPE: int(vehicle.get("vehicle_type", 0) or 0),
            CONF_VEHICLE_NAME: str(
                vehicle.get("selfDefinedName")
                or vehicle.get("vehicle_name")
                or "Navimow"
            ),
            CONF_MODEL: str(vehicle.get("subType", "")),
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = str(user_input[CONF_EMAIL]).strip()
            try:
                self._vehicles = await self._authenticate(
                    self._email,
                    user_input[CONF_PASSWORD],
                )
            except PassportAuthError:
                errors["base"] = "invalid_auth"
            except (PassportError, NavimowError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during private Navimow login")
                errors["base"] = "unknown"
            else:
                if not self._vehicles:
                    return await self.async_step_manual()
                configured = {entry.unique_id for entry in self._async_current_entries()}
                remaining = [
                    vehicle
                    for vehicle in self._vehicles
                    if str(vehicle.get("vehicle_sn")) not in configured
                ]
                if not remaining:
                    return await self.async_step_manual()
                self._vehicles = remaining
                # Always show the mower selector, even when only one unconfigured
                # mower remains. The confirmation makes the selected private
                # mower explicit before the separate official OAuth step starts.
                return await self.async_step_select_vehicle()

        return self.async_show_form(
            step_id="user",
            data_schema=_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_select_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            serial = str(user_input[CONF_VEHICLE_SN])
            vehicle = next(
                (
                    item
                    for item in self._vehicles
                    if str(item.get("vehicle_sn")) == serial
                ),
                self._vehicles[0],
            )
            return await self._prepare_vehicle(vehicle)

        choices = {
            str(vehicle.get("vehicle_sn")): (
                f"{vehicle.get('selfDefinedName') or vehicle.get('vehicle_name') or 'Navimow'} "
                f"({vehicle.get('vehicle_sn')})"
            )
            for vehicle in self._vehicles
        }
        return self.async_show_form(
            step_id="select_vehicle",
            data_schema=vol.Schema(
                {vol.Required(CONF_VEHICLE_SN): vol.In(choices)}
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate a shared mower by serial when auth-list is empty."""
        errors: dict[str, str] = {}
        if user_input is not None:
            serial = str(user_input[CONF_VEHICLE_SN]).strip().upper()

            def _probe() -> dict[str, Any]:
                assert self._client is not None
                index = self._client.index2(serial) or {}
                try:
                    device = self._client.device_info(serial) or {}
                except NavimowError:
                    device = {}
                return {"index": index, "device": device}

            try:
                probe = await self.hass.async_add_executor_job(_probe)
            except NavimowError:
                errors["base"] = "vehicle_not_found"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating shared mower serial")
                errors["base"] = "unknown"
            else:
                index = probe["index"]
                if not index:
                    errors["base"] = "vehicle_not_found"
                else:
                    device = probe["device"]
                    vehicle_type = (
                        index.get("vehicle_type")
                        or index.get("vehicleType")
                        or 160000001
                    )
                    vehicle = {
                        "vehicle_sn": serial,
                        "vehicle_type": int(vehicle_type or 160000001),
                        "vehicle_name": (
                            user_input.get(CONF_VEHICLE_NAME)
                            or device.get("model")
                            or "Navimow"
                        ),
                        "subType": device.get("model", ""),
                    }
                    return await self._prepare_vehicle(vehicle)

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VEHICLE_SN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Optional(CONF_VEHICLE_NAME, default="Navimow"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                }
            ),
            errors=errors,
        )

    async def _prepare_vehicle(self, vehicle: dict[str, Any]) -> ConfigFlowResult:
        serial = str(vehicle.get("vehicle_sn", ""))
        private_data = self._private_entry_data(vehicle)

        linked_entry = self._linked_entry()
        if linked_entry is not None:
            await self.async_set_unique_id(serial)
            self._abort_if_unique_id_mismatch()
            self._pending_data = private_data
            if self._reauth_mode == "private":
                return self.async_update_reload_and_abort(
                    linked_entry,
                    data_updates=private_data,
                )
            return await self.async_step_link_oauth()

        await self.async_set_unique_id(serial)
        self._abort_if_unique_id_configured()
        self._pending_data = private_data
        return await self.async_step_link_oauth()

    # --------------------------------------------------------------- OAuth link
    async def async_step_link_oauth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain and start the official Smart Home OAuth browser flow."""
        if self._pending_data is None and self._linked_entry() is None:
            return self.async_abort(reason="unknown")
        if user_input is None:
            return self.async_show_form(step_id="link_oauth", data_schema=vol.Schema({}))
        async_register_oauth_implementation(self.hass)
        return await self.async_step_pick_implementation()

    async def _async_validate_oauth_mower(
        self,
        oauth_data: dict[str, Any],
        serial: str,
    ) -> str | None:
        token = oauth_data.get("token") or {}
        access_token = token.get("access_token")
        if not access_token:
            return None
        from mower_sdk.api import MowerAPI

        api = MowerAPI(
            session=async_get_clientsession(self.hass),
            token=str(access_token),
            base_url=API_BASE_URL,
        )
        devices = await api.async_get_devices()
        serial_upper = serial.upper()
        for device in devices:
            if serial_upper in _device_candidates(device):
                return str(getattr(device, "id", "") or "")
        if len(devices) == 1:
            return str(getattr(devices[0], "id", "") or "")
        return None

    async def async_oauth_create_entry(
        self, oauth_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Combine OAuth tokens with the already validated private-cloud data."""
        existing = self._linked_entry()
        private_data = self._pending_data or (dict(existing.data) if existing else None)
        if private_data is None:
            return self.async_abort(reason="unknown")
        serial = str(private_data.get(CONF_VEHICLE_SN, ""))
        try:
            oauth_device_id = await self._async_validate_oauth_mower(
                oauth_data,
                serial,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not validate the Smart Home OAuth mower")
            return self.async_abort(reason="oauth_cannot_connect")
        if not oauth_device_id:
            return self.async_abort(reason="oauth_mower_not_found")

        combined = {
            **private_data,
            **oauth_data,
            CONF_OAUTH_DEVICE_ID: oauth_device_id,
            CONF_API_BASE_URL: API_BASE_URL,
        }
        if existing is not None:
            return self.async_update_reload_and_abort(
                existing,
                data_updates=combined,
            )

        return self.async_create_entry(
            title=str(combined.get(CONF_VEHICLE_NAME) or "Navimower"),
            data=combined,
        )

    # ------------------------------------------------------------------ reauth
    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        self._email = entry.data.get(CONF_EMAIL)
        self._device_id = entry.data.get(CONF_DEVICE_ID)
        requested = str((entry_data or {}).get("reauth_type") or "")
        if requested == "private":
            self._reauth_mode = "private"
            return await self.async_step_reauth_private()
        if requested == "oauth":
            self._reauth_mode = "oauth"
            self._pending_data = dict(self._linked_entry().data)
            return await self.async_step_link_oauth()
        return await self.async_step_reauth_select()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user replace private-cloud and/or Smart Home OAuth data."""
        entry = self._get_reconfigure_entry()
        self._email = entry.data.get(CONF_EMAIL)
        self._device_id = entry.data.get(CONF_DEVICE_ID)
        return await self.async_step_reauth_select(user_input)

    async def async_step_reauth_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._reauth_mode = str(user_input["reauth_mode"])
            if self._reauth_mode == "oauth":
                self._pending_data = dict(self._linked_entry().data)
                return await self.async_step_link_oauth()
            return await self.async_step_reauth_private()
        return self.async_show_form(
            step_id="reauth_select",
            data_schema=vol.Schema(
                {
                    vol.Required("reauth_mode", default="both"): vol.In(
                        {
                            "private": "Private cloud only",
                            "oauth": "Smart Home OAuth only",
                            "both": "Both connections",
                        }
                    )
                }
            ),
        )

    async def async_step_reauth_private(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = str(user_input.get(CONF_EMAIL) or self._email or "").strip()
            self._email = email
            try:
                self._vehicles = await self._authenticate(
                    email,
                    user_input[CONF_PASSWORD],
                )
            except PassportAuthError:
                errors["base"] = "invalid_auth"
            except (PassportError, NavimowError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during private-cloud reauth")
                errors["base"] = "unknown"
            else:
                linked_entry = self._linked_entry()
                entry_data = linked_entry.data if linked_entry is not None else {}
                serial = entry_data.get(CONF_VEHICLE_SN)
                vehicle = next(
                    (
                        item
                        for item in self._vehicles
                        if str(item.get("vehicle_sn")) == serial
                    ),
                    None,
                )
                if vehicle is None:
                    vehicle = {
                        "vehicle_sn": serial or "",
                        "vehicle_type": entry_data.get(CONF_VEHICLE_TYPE, 160000001),
                        "vehicle_name": entry_data.get(CONF_VEHICLE_NAME, "Navimow"),
                        "subType": entry_data.get(CONF_MODEL, ""),
                    }
                return await self._prepare_vehicle(vehicle)

        return self.async_show_form(
            step_id="reauth_private",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=self._email or ""): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.EMAIL,
                            autocomplete="username",
                        )
                    ),
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="current-password",
                        )
                    ),
                }
            ),
            errors=errors,
        )

    # ---------------------------------------------------------------- options
    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> "NavimowOptionsFlow":
        return NavimowOptionsFlow()


class NavimowOptionsFlow(OptionsFlowWithReload):
    """Manage general history, user-friendly gates and local channels."""

    def __init__(self) -> None:
        self._gate_index: int | None = None
        self._channel_index: int | None = None

    def _options(self) -> dict[str, Any]:
        return dict(self.config_entry.options)

    def _coordinator(self) -> Any | None:
        return (self.hass.data.get(DOMAIN) or {}).get(self.config_entry.entry_id)

    def _zone_choices(self) -> dict[str, str]:
        coordinator = self._coordinator()
        data = getattr(coordinator, "data", None) or {}
        zones = data.get("zones") or ((data.get("map") or {}).get("zones")) or []
        choices: dict[str, str] = {}
        for zone in zones:
            if not isinstance(zone, dict) or zone.get("id") is None:
                continue
            zone_id = str(int(zone["id"]))
            choices[zone_id] = f"{zone.get('name') or f'Zone {zone_id}'} (ID {zone_id})"
        if not choices:
            raw = self.config_entry.options.get(OPT_ZONES, "")
            for item in str(raw).split(","):
                if ":" in item:
                    zone_id, name = item.split(":", 1)
                    if zone_id.strip().isdigit():
                        choices[zone_id.strip()] = name.strip() or f"Zone {zone_id.strip()}"
        # Existing gate definitions remain editable during a temporary map/cloud
        # outage, even when the decoded zone list is not currently available.
        for gate in self._gates():
            for zone_id in gate.zones:
                choices.setdefault(str(zone_id), f"Zone {zone_id} (ID {zone_id})")
        return choices

    def _gates(self) -> list[NavimowerGate]:
        return parse_gates(self.config_entry.options.get(OPT_GATES))

    def _channels(self) -> list[NavimowerChannel]:
        return parse_channels(self.config_entry.options.get(OPT_CHANNELS))

    def _save(self, **updates: Any) -> ConfigFlowResult:
        """Finish the flow with the complete updated options mapping."""
        options = self._options()
        options.update(updates)
        return self.async_create_entry(data=options)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        del user_input
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "gates", "channels"],
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = self._options()
        if user_input is not None:
            return self._save(
                **{
                    OPT_TRAIL_RETENTION_DAYS: int(user_input[OPT_TRAIL_RETENTION_DAYS]),
                    OPT_INCLUDE_RETURN_TRAIL: bool(user_input[OPT_INCLUDE_RETURN_TRAIL]),
                    OPT_DIAGNOSTICS_DETAIL: str(user_input[OPT_DIAGNOSTICS_DETAIL]),
                }
            )
        retention_labels = {
            3: "3 days",
            7: "7 days",
            14: "14 days",
            30: "30 days",
            0: "Unlimited",
        }
        return self.async_show_form(
            step_id="general",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        OPT_TRAIL_RETENTION_DAYS,
                        default=int(
                            options.get(
                                OPT_TRAIL_RETENTION_DAYS,
                                DEFAULT_TRAIL_RETENTION_DAYS,
                            )
                        ),
                    ): vol.In(
                        {value: retention_labels[value] for value in TRAIL_RETENTION_OPTIONS}
                    ),
                    vol.Required(
                        OPT_INCLUDE_RETURN_TRAIL,
                        default=bool(
                            options.get(
                                OPT_INCLUDE_RETURN_TRAIL,
                                DEFAULT_INCLUDE_RETURN_TRAIL,
                            )
                        ),
                    ): bool,
                    vol.Required(
                        OPT_DIAGNOSTICS_DETAIL,
                        default=str(
                            options.get(
                                OPT_DIAGNOSTICS_DETAIL,
                                DEFAULT_DIAGNOSTICS_DETAIL,
                            )
                        ),
                    ): vol.In(
                        {
                            "standard": "Standard",
                            "extended": "Extended",
                        }
                    ),
                }
            ),
        )

    async def async_step_gates(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        del user_input
        menu = ["gate_add"]
        if self._gates():
            menu.extend(["gate_edit_select", "gate_delete"])
        return self.async_show_menu(step_id="gates", menu_options=menu)

    def _gate_schema(self, gate: NavimowerGate | None = None) -> vol.Schema:
        zones = self._zone_choices()
        if gate is not None:
            zones.setdefault(str(gate.zone_a), f"Zone {gate.zone_a} (ID {gate.zone_a})")
            zones.setdefault(str(gate.zone_b), f"Zone {gate.zone_b} (ID {gate.zone_b})")
        default_a = str(gate.zone_a) if gate else (next(iter(zones), ""))
        default_b = str(gate.zone_b) if gate else (
            next((value for value in zones if value != default_a), default_a)
        )
        return vol.Schema(
            {
                vol.Required("name", default=gate.name if gate else "Gate"): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Required("zone_a", default=default_a): vol.In(zones),
                vol.Required("zone_b", default=default_b): vol.In(zones),
                vol.Required(
                    "bidirectional",
                    default=gate.bidirectional if gate else True,
                ): bool,
                vol.Required(
                    "close_delay",
                    default=gate.close_delay if gate else 20,
                ): vol.In(
                    {
                        0: "Immediately",
                        10: "10 seconds",
                        20: "20 seconds",
                        30: "30 seconds",
                    }
                ),
            }
        )

    async def async_step_gate_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        zones = self._zone_choices()
        if len(zones) < 2:
            return self.async_abort(reason="zones_not_available")
        if user_input is not None:
            if user_input["zone_a"] == user_input["zone_b"]:
                errors["base"] = "same_zone"
            else:
                gates = [gate.as_dict() for gate in self._gates()]
                gates.append(
                    {
                        "name": str(user_input["name"]).strip() or "Gate",
                        "zone_a": int(user_input["zone_a"]),
                        "zone_b": int(user_input["zone_b"]),
                        "bidirectional": bool(user_input["bidirectional"]),
                        "close_delay": int(user_input["close_delay"]),
                    }
                )
                parsed = parse_gates(gates)
                if len(parsed) != len(gates):
                    errors["base"] = "duplicate_gate"
                else:
                    return self._save(**{OPT_GATES: [gate.as_dict() for gate in parsed]})
        return self.async_show_form(
            step_id="gate_add",
            data_schema=self._gate_schema(),
            errors=errors,
        )

    async def async_step_gate_edit_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        gates = self._gates()
        if not gates:
            return self.async_abort(reason="no_gates")
        choices = {str(index): gate.name for index, gate in enumerate(gates)}
        if user_input is not None:
            self._gate_index = int(user_input["gate"])
            return await self.async_step_gate_edit()
        return self.async_show_form(
            step_id="gate_edit_select",
            data_schema=vol.Schema({vol.Required("gate"): vol.In(choices)}),
        )

    async def async_step_gate_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        gates = self._gates()
        if self._gate_index is None or self._gate_index >= len(gates):
            return self.async_abort(reason="no_gates")
        gate = gates[self._gate_index]
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input["zone_a"] == user_input["zone_b"]:
                errors["base"] = "same_zone"
            else:
                replacement = NavimowerGate(
                    name=str(user_input["name"]).strip() or "Gate",
                    zone_a=int(user_input["zone_a"]),
                    zone_b=int(user_input["zone_b"]),
                    bidirectional=bool(user_input["bidirectional"]),
                    close_delay=int(user_input["close_delay"]),
                )
                gates[self._gate_index] = replacement
                parsed = parse_gates([item.as_dict() for item in gates])
                if len(parsed) != len(gates):
                    errors["base"] = "duplicate_gate"
                else:
                    return self._save(**{OPT_GATES: [item.as_dict() for item in parsed]})
        return self.async_show_form(
            step_id="gate_edit",
            data_schema=self._gate_schema(gate),
            errors=errors,
        )

    async def async_step_gate_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        gates = self._gates()
        if not gates:
            return self.async_abort(reason="no_gates")
        choices = {str(index): gate.name for index, gate in enumerate(gates)}
        if user_input is not None:
            index = int(user_input["gate"])
            remaining = [gate for pos, gate in enumerate(gates) if pos != index]
            return self._save(**{OPT_GATES: [gate.as_dict() for gate in remaining]})
        return self.async_show_form(
            step_id="gate_delete",
            data_schema=vol.Schema({vol.Required("gate"): vol.In(choices)}),
        )

    async def async_step_channels(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        del user_input
        menu = ["channel_add"]
        if self._channels():
            menu.extend(["channel_edit_select", "channel_delete"])
        return self.async_show_menu(step_id="channels", menu_options=menu)

    @staticmethod
    def _channel_schema(channel: NavimowerChannel | None = None) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required("name", default=channel.name if channel else "Channel"): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Required(
                    "x_min", default=channel.x_min if channel else 0.0
                ): vol.Coerce(float),
                vol.Required(
                    "x_max", default=channel.x_max if channel else 1.0
                ): vol.Coerce(float),
                vol.Required(
                    "y_min", default=channel.y_min if channel else 0.0
                ): vol.Coerce(float),
                vol.Required(
                    "y_max", default=channel.y_max if channel else 1.0
                ): vol.Coerce(float),
            }
        )

    async def async_step_channel_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            channels = [channel.as_dict() for channel in self._channels()]
            channels.append(dict(user_input))
            parsed = parse_channels(channels)
            if len(parsed) != len(channels):
                return self.async_show_form(
                    step_id="channel_add",
                    data_schema=self._channel_schema(),
                    errors={"base": "duplicate_channel"},
                )
            return self._save(**{OPT_CHANNELS: [channel.as_dict() for channel in parsed]})
        return self.async_show_form(
            step_id="channel_add",
            data_schema=self._channel_schema(),
        )

    async def async_step_channel_edit_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        channels = self._channels()
        if not channels:
            return self.async_abort(reason="no_channels")
        choices = {str(index): channel.name for index, channel in enumerate(channels)}
        if user_input is not None:
            self._channel_index = int(user_input["channel"])
            return await self.async_step_channel_edit()
        return self.async_show_form(
            step_id="channel_edit_select",
            data_schema=vol.Schema({vol.Required("channel"): vol.In(choices)}),
        )

    async def async_step_channel_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        channels = self._channels()
        if self._channel_index is None or self._channel_index >= len(channels):
            return self.async_abort(reason="no_channels")
        channel = channels[self._channel_index]
        if user_input is not None:
            items = [item.as_dict() for item in channels]
            items[self._channel_index] = dict(user_input)
            parsed = parse_channels(items)
            if len(parsed) != len(items):
                return self.async_show_form(
                    step_id="channel_edit",
                    data_schema=self._channel_schema(channel),
                    errors={"base": "duplicate_channel"},
                )
            return self._save(**{OPT_CHANNELS: [item.as_dict() for item in parsed]})
        return self.async_show_form(
            step_id="channel_edit",
            data_schema=self._channel_schema(channel),
        )

    async def async_step_channel_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        channels = self._channels()
        if not channels:
            return self.async_abort(reason="no_channels")
        choices = {str(index): channel.name for index, channel in enumerate(channels)}
        if user_input is not None:
            index = int(user_input["channel"])
            remaining = [item for pos, item in enumerate(channels) if pos != index]
            return self._save(**{OPT_CHANNELS: [item.as_dict() for item in remaining]})
        return self.async_show_form(
            step_id="channel_delete",
            data_schema=vol.Schema({vol.Required("channel"): vol.In(choices)}),
        )
