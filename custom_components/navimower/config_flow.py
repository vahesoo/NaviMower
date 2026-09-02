"""Navimower config flow with experimental Custom Area import support.

The stable beta29 flow is kept in ``config_flow_base`` so the Custom Area
experiment can evolve without disturbing the already field-tested login,
scheduler, gate and legacy channel flows.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .account import private_account_entries
from .config_flow_base import NavimowConfigFlow
from .config_flow_base import NavimowOptionsFlow as _BaseNavimowOptionsFlow
from .const import (
    CONF_EMAIL,
    DOMAIN,
    OPT_GOOGLE_MAPS_API_KEY,
    OPT_SCHEDULE_CUSTOM_QUEUE,
    OPT_SCHEDULE_ORDER_MODE,
    SCHEDULE_ORDER_AUTOMATIC,
    SCHEDULE_ORDER_CUSTOM,
)
from .custom_area import (
    OPT_CUSTOM_AREAS,
    create_custom_area,
    find_new_polygons,
    normalize_polygon,
    parse_custom_areas,
    polygon_area_m2,
    polygon_centroid,
)
from .map_underlay import (
    GoogleMapTilesError,
    get_map_underlay_manager,
    google_maps_api_key_for_entry,
    google_session_locale,
)

_LOGGER = logging.getLogger(__name__)
_PASSIVE_DISCOVERY_OPTION = "passive_discovery"
_CLEAR_GOOGLE_MAPS_API_KEY = "clear_google_maps_api_key"


class NavimowOptionsFlow(_BaseNavimowOptionsFlow):
    """Extend production options with Custom Areas and map-underlay setup."""

    def __init__(self) -> None:
        super().__init__()
        self._custom_area_baseline: list[list[list[float]]] | None = None
        self._custom_area_baseline_revision: str | None = None
        self._custom_area_candidate: list[list[float]] | None = None
        self._pending_schedule_order_mode: str | None = None

    def _custom_areas(self):
        return parse_custom_areas(self.config_entry.options.get(OPT_CUSTOM_AREAS))

    def _off_limit_polygons(self) -> list[list[list[float]]]:
        coordinator = self._coordinator()
        data = getattr(coordinator, "data", None) or {}
        map_data = data.get("map") if isinstance(data.get("map"), dict) else {}
        result: list[list[list[float]]] = []
        for raw in map_data.get("off_limit_areas") or []:
            polygon = normalize_polygon(raw)
            if polygon is not None:
                result.append(polygon)
        return result

    def _map_revision(self) -> str:
        coordinator = self._coordinator()
        data = getattr(coordinator, "data", None) or {}
        map_data = data.get("map") if isinstance(data.get("map"), dict) else {}
        return str(map_data.get("revision") or map_data.get("map_version") or "")

    async def _refresh_map_for_custom_area(self) -> None:
        """Bypass endpoint TTLs once so capture/detect sees the current app map."""
        coordinator = self._coordinator()
        if coordinator is None:
            return
        statuses = getattr(coordinator, "_endpoint_status", {})
        for endpoint in ("index2", "location", "map_list"):
            status = statuses.get(endpoint)
            if isinstance(status, dict):
                status["last_attempt_mono"] = None
                status["last_attempt_utc"] = None
        await coordinator.async_request_refresh()

    def _save(self, **updates: Any) -> ConfigFlowResult:
        """Save production options while keeping the beta43 discovery switch."""
        options = self._options()
        options.pop("diagnostics_detail", None)
        options.update(updates)
        return self.async_create_entry(data=options)

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Temporarily expose passive MQTT discovery for gate-state field tests."""
        if user_input is not None:
            enabled = bool(user_input.get(_PASSIVE_DISCOVERY_OPTION, False))
            payload = dict(user_input)
            payload.pop(_PASSIVE_DISCOVERY_OPTION, None)
            result = await super().async_step_general(payload)
            if result.get("type") == "create_entry":
                data = dict(result.get("data") or {})
                data[_PASSIVE_DISCOVERY_OPTION] = enabled
                result["data"] = data
            return result

        result = await super().async_step_general(None)
        if result.get("type") == "form" and result.get("step_id") == "general":
            schema = dict(result["data_schema"].schema)
            schema[
                vol.Required(
                    _PASSIVE_DISCOVERY_OPTION,
                    default=bool(
                        self._options().get(_PASSIVE_DISCOVERY_OPTION, False)
                    ),
                )
            ] = bool
            result["data_schema"] = vol.Schema(schema)
        return result

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        del user_input
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "general",
                "navimower_schedule",
                "gates",
                "custom_areas",
                "channels",
                "map_underlay",
            ],
        )

    def _account_entries(self) -> list[Any]:
        return private_account_entries(
            self.hass.config_entries.async_entries(DOMAIN),
            (self.config_entry.data or {}).get(CONF_EMAIL),
        )

    async def async_step_map_underlay(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure one account-scoped Google Map Tiles API key."""
        errors: dict[str, str] = {}
        configured = bool(
            google_maps_api_key_for_entry(self.hass, self.config_entry)
        )
        manager = get_map_underlay_manager(self.hass)
        account_key = manager.account_key(self.config_entry)

        if user_input is not None:
            clear_key = bool(user_input.get(_CLEAR_GOOGLE_MAPS_API_KEY, False))
            api_key = str(user_input.get(OPT_GOOGLE_MAPS_API_KEY) or "").strip()

            if clear_key:
                options = self._options()
                options.pop(OPT_GOOGLE_MAPS_API_KEY, None)
                for peer in self._account_entries():
                    if peer.entry_id == self.config_entry.entry_id:
                        continue
                    peer_options = dict(peer.options)
                    if OPT_GOOGLE_MAPS_API_KEY in peer_options:
                        peer_options.pop(OPT_GOOGLE_MAPS_API_KEY, None)
                        self.hass.config_entries.async_update_entry(
                            peer,
                            options=peer_options,
                        )
                manager.invalidate_account(account_key)
                return self.async_create_entry(data=options)

            if api_key:
                language, region = google_session_locale(
                    self.hass,
                    self._coordinator(),
                )
                try:
                    await manager.async_validate_key(
                        async_get_clientsession(self.hass),
                        account_key,
                        api_key,
                        language=language,
                        region=region,
                    )
                except GoogleMapTilesError as err:
                    errors["base"] = (
                        "google_maps_api_cannot_connect"
                        if err.kind == "connection_error"
                        else "google_maps_api_invalid"
                    )
                else:
                    for peer in self._account_entries():
                        if peer.entry_id == self.config_entry.entry_id:
                            continue
                        peer_options = dict(peer.options)
                        peer_options[OPT_GOOGLE_MAPS_API_KEY] = api_key
                        self.hass.config_entries.async_update_entry(
                            peer,
                            options=peer_options,
                        )
                    return self._save(**{OPT_GOOGLE_MAPS_API_KEY: api_key})
            elif not errors:
                # Empty password field means keep the existing shared key.
                return self._save()

        return self.async_show_form(
            step_id="map_underlay",
            data_schema=vol.Schema(
                {
                    vol.Optional(OPT_GOOGLE_MAPS_API_KEY, default=""): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="off",
                        )
                    ),
                    vol.Required(
                        _CLEAR_GOOGLE_MAPS_API_KEY,
                        default=False,
                    ): bool,
                }
            ),
            errors=errors,
            description_placeholders={
                "google_api_status": "Configured" if configured else "Not configured",
            },
        )

    def _schedule_order_selector(self, mode: str):
        return SelectSelector(
            SelectSelectorConfig(
                options=[
                    {"value": SCHEDULE_ORDER_AUTOMATIC, "label": "Automatic order"},
                    {"value": SCHEDULE_ORDER_CUSTOM, "label": "Custom order"},
                ],
                mode=SelectSelectorMode.LIST,
            )
        )

    def _seed_custom_queue(self, zone_ids: Any) -> list[str]:
        selected = {str(value) for value in (zone_ids or [])}
        coordinator = self._coordinator()
        data = getattr(coordinator, "data", None) or {}
        rows = []
        for row in data.get("zone_states") or []:
            if not isinstance(row, dict):
                continue
            zone_id = str(row.get("id") or "")
            completed = row.get("last_completed_at")
            if zone_id in selected and completed:
                rows.append((str(completed), zone_id))
        rows.sort(key=lambda item: (item[0], item[1]))
        return [zone_id for _, zone_id in rows]

    def _apply_schedule_order(self, result: ConfigFlowResult, mode: str) -> ConfigFlowResult:
        if result.get("type") == "form" and result.get("step_id") == "navimower_schedule":
            schema = dict(result["data_schema"].schema)
            schema[vol.Required(OPT_SCHEDULE_ORDER_MODE, default=mode)] = self._schedule_order_selector(mode)
            result["data_schema"] = vol.Schema(schema)
            return result
        if result.get("type") == "create_entry":
            data = dict(result.get("data") or {})
            data[OPT_SCHEDULE_ORDER_MODE] = mode
            if mode == SCHEDULE_ORDER_CUSTOM:
                selected = data.get("navimower_schedule_zone_ids", self._options().get("navimower_schedule_zone_ids", []))
                existing = list(self._options().get(OPT_SCHEDULE_CUSTOM_QUEUE, []) or [])
                selected_set = {str(value) for value in (selected or [])}
                queue = [str(value) for value in existing if str(value) in selected_set]
                if not queue:
                    queue = self._seed_custom_queue(selected)
                data[OPT_SCHEDULE_CUSTOM_QUEUE] = queue
            result["data"] = data
        return result

    async def async_step_navimower_schedule(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = self._options()
        mode = str(options.get(OPT_SCHEDULE_ORDER_MODE, SCHEDULE_ORDER_AUTOMATIC))
        payload = None if user_input is None else dict(user_input)
        if payload is not None:
            mode = str(payload.pop(OPT_SCHEDULE_ORDER_MODE, mode) or SCHEDULE_ORDER_AUTOMATIC)
            if mode not in {SCHEDULE_ORDER_AUTOMATIC, SCHEDULE_ORDER_CUSTOM}:
                mode = SCHEDULE_ORDER_AUTOMATIC
            self._pending_schedule_order_mode = mode
        result = await super().async_step_navimower_schedule(payload)
        return self._apply_schedule_order(result, mode)

    async def async_step_navimower_schedule_window(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        result = await super().async_step_navimower_schedule_window(user_input)
        mode = self._pending_schedule_order_mode or str(
            self._options().get(OPT_SCHEDULE_ORDER_MODE, SCHEDULE_ORDER_AUTOMATIC)
        )
        return self._apply_schedule_order(result, mode)

    async def async_step_custom_areas(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        del user_input
        menu = ["custom_area_add"]
        if self._custom_areas():
            menu.append("custom_area_delete")
        return self.async_show_menu(step_id="custom_areas", menu_options=menu)

    async def async_step_custom_area_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Refresh and capture the baseline as soon as Add Custom Area is selected."""
        del user_input
        errors: dict[str, str] = {}

        try:
            await self._refresh_map_for_custom_area()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not refresh Navimow map before Custom Area capture")
            errors["base"] = "custom_area_refresh_failed"
        else:
            coordinator = self._coordinator()
            data = getattr(coordinator, "data", None) or {}
            map_data = data.get("map") if isinstance(data.get("map"), dict) else {}
            if not map_data or not isinstance(map_data.get("off_limit_areas"), list):
                errors["base"] = "custom_area_map_not_available"
            else:
                self._custom_area_baseline = self._off_limit_polygons()
                self._custom_area_baseline_revision = self._map_revision()
                self._custom_area_candidate = None
                return await self.async_step_custom_area_detect()

        # This retry form is only shown if the fresh baseline could not be captured.
        return self.async_show_form(
            step_id="custom_area_add",
            data_schema=vol.Schema({}),
            errors=errors,
        )

    async def async_step_custom_area_detect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Refresh the map and identify exactly one newly added off-limit polygon."""
        errors: dict[str, str] = {}
        if self._custom_area_baseline is None:
            return await self.async_step_custom_area_add()

        if user_input is not None:
            try:
                await self._refresh_map_for_custom_area()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Could not refresh Navimow map for Custom Area import")
                errors["base"] = "custom_area_refresh_failed"
            else:
                candidates = find_new_polygons(
                    self._custom_area_baseline,
                    self._off_limit_polygons(),
                )
                if not candidates:
                    errors["base"] = "custom_area_not_detected"
                elif len(candidates) > 1:
                    errors["base"] = "custom_area_multiple_detected"
                else:
                    self._custom_area_candidate = candidates[0]
                    return await self.async_step_custom_area_name()

        return self.async_show_form(
            step_id="custom_area_detect",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "baseline_revision": self._custom_area_baseline_revision or "unknown",
            },
        )

    async def async_step_custom_area_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Persist the detected polygon as a NaviMower-owned virtual area."""
        candidate = self._custom_area_candidate
        if candidate is None:
            return await self.async_step_custom_area_add()

        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input.get("name") or "").strip()
            existing = self._custom_areas()
            new_area = create_custom_area(name, candidate)
            if new_area is None:
                errors["base"] = "custom_area_invalid"
            elif any(area.slug == new_area.slug for area in existing):
                errors["base"] = "custom_area_duplicate"
            else:
                values = [area.as_dict() for area in existing]
                values.append(new_area.as_dict())
                return self._save(**{OPT_CUSTOM_AREAS: values})

        area = polygon_area_m2(candidate)
        centroid = polygon_centroid(candidate)
        return self.async_show_form(
            step_id="custom_area_name",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default="Custom area"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "area": f"{area:.2f}" if area is not None else "unknown",
                "points": str(len(candidate)),
                "centroid": (
                    f"{centroid[0]:.2f}, {centroid[1]:.2f}"
                    if centroid is not None
                    else "unknown"
                ),
            },
        )

    async def async_step_custom_area_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Delete only the local virtual polygon; never write the mower map."""
        areas = self._custom_areas()
        if not areas:
            return self.async_abort(reason="no_custom_areas")
        choices = {area.area_id: area.name for area in areas}
        if user_input is not None:
            area_id = str(user_input["custom_area"])
            remaining = [area for area in areas if area.area_id != area_id]
            return self._save(
                **{OPT_CUSTOM_AREAS: [area.as_dict() for area in remaining]}
            )
        return self.async_show_form(
            step_id="custom_area_delete",
            data_schema=vol.Schema({vol.Required("custom_area"): vol.In(choices)}),
        )


@callback
def _async_get_options_flow(entry: ConfigEntry) -> NavimowOptionsFlow:
    """Return the Custom Area options-flow extension for the registered handler."""
    del entry
    return NavimowOptionsFlow()


# NavimowConfigFlow was registered with Home Assistant when config_flow_base was
# imported. Swap only its options-flow factory; authentication remains unchanged.
NavimowConfigFlow.async_get_options_flow = staticmethod(_async_get_options_flow)
