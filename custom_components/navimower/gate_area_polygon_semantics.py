"""Options-flow support for exact polygon gate areas.

Legacy gate areas remain rectangular local X/Y bounds.  This extension adds an
optional JSON polygon field to the existing add/edit forms; when populated, the
polygon is authoritative and the stored min/max values become its compatibility
bounding box.
"""
from __future__ import annotations

import json
from typing import Any

import voluptuous as vol
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .channel import NavimowerChannel, parse_channels
from .config_flow_base import NavimowOptionsFlow

_INSTALLED = False
_ORIGINAL_CHANNEL_SCHEMA = NavimowOptionsFlow._channel_schema


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
    """Extend the legacy rectangle editor with an optional exact polygon."""
    schema = dict(_ORIGINAL_CHANNEL_SCHEMA(channel).schema)
    default = ""
    if channel is not None and channel.polygon is not None:
        default = json.dumps(
            [[x, y] for x, y in channel.polygon],
            separators=(",", ":"),
        )
    schema[
        vol.Optional("polygon", default=default)
    ] = vol.All(
        TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        _polygon_text,
    )
    return vol.Schema(schema)


def install_gate_area_polygon_semantics() -> None:
    """Add polygon editing while preserving all existing channel flow behavior."""
    global _INSTALLED
    if _INSTALLED:
        return
    NavimowOptionsFlow._channel_schema = staticmethod(_channel_schema)
    _INSTALLED = True
