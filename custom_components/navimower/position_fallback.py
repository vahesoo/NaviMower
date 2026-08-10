"""Dependency-free helpers for freshness-aware position fallback."""
from __future__ import annotations

import time
from typing import Any

CLOUD_GATE_FRESH_SECONDS = 30.0


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def cloud_report_age(report_time: Any, *, now_epoch: float | None = None) -> float | None:
    """Return age of the vendor's private-cloud pose timestamp in seconds."""
    value = _as_float(report_time)
    if value is None or value <= 0:
        return None
    if value > 10_000_000_000:
        value /= 1000.0
    now = time.time() if now_epoch is None else float(now_epoch)
    # Small negative values can happen because clocks are not perfectly aligned.
    return max(0.0, now - value)


def choose_position(
    *,
    mqtt_position: dict[str, Any] | None,
    mqtt_age: float | None,
    cloud_position: dict[str, Any] | None,
    cloud_report_time: Any,
    now_epoch: float | None = None,
    cloud_gate_max_age: float = CLOUD_GATE_FRESH_SECONDS,
) -> dict[str, Any]:
    """Choose display position and whether it is safe enough for gate logic.

    A caller must pass only an already-fresh MQTT pose. MQTT therefore always
    wins. Private-cloud X/Y remains useful for display even when its vendor
    timestamp is old, but is gate-usable only while that timestamp is recent.
    """
    if isinstance(mqtt_position, dict):
        return {
            "position": mqtt_position,
            "source": "mqtt",
            "age": mqtt_age,
            "stale": False,
            "gate_usable": True,
        }

    cloud_age = cloud_report_age(cloud_report_time, now_epoch=now_epoch)
    if isinstance(cloud_position, dict):
        gate_usable = bool(
            cloud_age is not None and cloud_age <= float(cloud_gate_max_age)
        )
        return {
            "position": cloud_position,
            "source": "private_cloud",
            "age": cloud_age,
            "stale": not gate_usable,
            "gate_usable": gate_usable,
        }

    return {
        "position": None,
        "source": "unavailable",
        "age": None,
        "stale": True,
        "gate_usable": False,
    }
