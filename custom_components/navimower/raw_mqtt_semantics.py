"""Retain bounded exact MQTT payloads for explicit local raw-data exports."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from .mqtt import NavimowerMqttBridge

_INSTALLED = False
_ORIGINAL_RECORD = NavimowerMqttBridge._record_message_inventory


def _record_message_inventory(
    self: NavimowerMqttBridge,
    topic: str,
    payload: bytes,
    incoming_device_id: str,
) -> None:
    _ORIGINAL_RECORD(self, topic, payload, incoming_device_id)
    if incoming_device_id != getattr(self, "_device_id", ""):
        return

    cache = getattr(self, "_raw_message_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        self._raw_message_cache = cache

    topic_text = str(topic)
    raw = bytes(payload or b"")
    cache[topic_text] = {
        "seen_utc": datetime.now(UTC).isoformat(),
        "incoming_device_id": str(incoming_device_id),
        "payload_bytes": len(raw),
        "payload_utf8": raw.decode("utf-8", errors="replace"),
    }
    # Exact latest message per topic is enough for the explicit snapshot action;
    # bound topic count so leaving this enabled has negligible memory impact.
    while len(cache) > 64:
        del cache[next(iter(cache))]


def raw_message_diagnostics(self: NavimowerMqttBridge) -> dict[str, Any]:
    return deepcopy(getattr(self, "_raw_message_cache", {}) or {})


def install_raw_mqtt_semantics() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    NavimowerMqttBridge._record_message_inventory = _record_message_inventory
    NavimowerMqttBridge.raw_message_diagnostics = raw_message_diagnostics
    _INSTALLED = True


__all__ = ["install_raw_mqtt_semantics"]
