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
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .config_flow_base import NavimowConfigFlow
from .config_flow_base import NavimowOptionsFlow as _BaseNavimowOptionsFlow
from .custom_area import (
    OPT_CUSTOM_AREAS,
    create_custom_area,
    find_new_polygons,
    normalize_polygon,
    parse_custom_areas,
    polygon_area_m2,
    polygon_centroid,
)

_LOGGER = logging.getLogger(__name__)


class NavimowOptionsFlow(_BaseNavimowOptionsFlow):
    """Extend the production options flow with Custom Area capture/import."""

    def __init__(self) -> None:
        super().__init__()
        self._custom_area_baseline: list[list[list[float]]] | None = None
        self._custom_area_baseline_revision: str | None = None
        self._custom_area_candidate: list[list[float]] | None = None

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
            ],
        )

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
