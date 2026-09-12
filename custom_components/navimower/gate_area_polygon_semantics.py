"""Options-flow support for exact polygon gate areas.

Legacy gate areas remain rectangular local X/Y bounds. This extension adds an
optional JSON polygon field to the existing add/edit forms; when populated, the
polygon is authoritative and the stored min/max values become its compatibility
bounding box.
"""
from __future__ import annotations

import json
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .channel import NavimowerChannel, parse_channels
from .config_flow_base import NavimowOptionsFlow

_INSTALLED = False
_ORIGINAL_CHANNEL_SCHEMA = NavimowOptionsFlow._channel_schema
_ORIGINAL_CHANNEL_ADD = NavimowOptionsFlow.async_step_channel_add
_ORIGINAL_CHANNEL_EDIT = NavimowOptionsFlow.async_step_channel_edit


def _polygon_text(value: Any) -> str:
    """Validate optional JSON polygon text without changing rectangle behavior."""
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = parse_channels([{"name": "Polygon validation", "polygon": text}])
    if len(parsed) != 1 or parsed[0].polygon is None:
        raise vol.Invalid(
            "Use JSON X/Y points such as [[-28.75,8],[-23.5,5.25],[-19.25,10.75],[-24,14.75]]"
        )
    return text


def _channel_schema(channel: NavimowerChannel | None = None) -> vol.Schema:
    """Extend the legacy rectangle editor with a serializable polygon selector."""
    schema = dict(_ORIGINAL_CHANNEL_SCHEMA(channel).schema)
    default = ""
    if channel is not None and channel.polygon is not None:
        default = json.dumps(
            [[x, y] for x, y in channel.polygon],
            separators=(",", ":"),
        )
    # Home Assistant must serialize selectors directly for Options Flow forms.
    # Wrapping TextSelector in vol.All(..., custom_validator) causes the form
    # schema websocket response to fail before the dialog can be rendered.
    schema[vol.Optional("polygon", default=default)] = TextSelector(
        TextSelectorConfig(type=TextSelectorType.TEXT)
    )
    return vol.Schema(schema)


def _invalid_polygon(user_input: dict[str, Any] | None) -> bool:
    """Return whether the submitted optional polygon text is malformed."""
    if user_input is None:
        return False
    try:
        _polygon_text(user_input.get("polygon", ""))
    except vol.Invalid:
        return True
    return False


async def _async_step_channel_add(
    self: NavimowOptionsFlow,
    user_input: dict[str, Any] | None = None,
) -> ConfigFlowResult:
    """Validate polygon JSON outside the serialized selector schema."""
    if _invalid_polygon(user_input):
        return self.async_show_form(
            step_id="channel_add",
            data_schema=self._channel_schema(),
            errors={"base": "duplicate_channel"},
        )
    return await _ORIGINAL_CHANNEL_ADD(self, user_input)


async def _async_step_channel_edit(
    self: NavimowOptionsFlow,
    user_input: dict[str, Any] | None = None,
) -> ConfigFlowResult:
    """Validate edited polygon JSON outside the serialized selector schema."""
    if _invalid_polygon(user_input):
        channels = self._channels()
        channel = (
            channels[self._channel_index]
            if self._channel_index is not None and self._channel_index < len(channels)
            else None
        )
        return self.async_show_form(
            step_id="channel_edit",
            data_schema=self._channel_schema(channel),
            errors={"base": "duplicate_channel"},
        )
    return await _ORIGINAL_CHANNEL_EDIT(self, user_input)


def install_gate_area_polygon_semantics() -> None:
    """Add polygon editing while preserving all existing channel flow behavior."""
    global _INSTALLED
    if _INSTALLED:
        return
    NavimowOptionsFlow._channel_schema = staticmethod(_channel_schema)
    NavimowOptionsFlow.async_step_channel_add = _async_step_channel_add
    NavimowOptionsFlow.async_step_channel_edit = _async_step_channel_edit
    _INSTALLED = True
