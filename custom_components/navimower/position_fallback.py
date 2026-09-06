"""Dependency-free helpers for freshness-aware position fallback."""
from __future__ import annotations

import math
import time
from typing import Any

CLOUD_GATE_FRESH_SECONDS = 30.0
CLOUD_CLOCK_FUTURE_TOLERANCE_SECONDS = 30.0


def _as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def cloud_report_age(
    report_time: Any,
    *,
    now_epoch: float | None = None,
) -> float | None:
    """Return a validated private-cloud pose age in seconds.

    A small future skew is tolerated because the mower/cloud and Home Assistant
    clocks are not guaranteed to be identical. Non-finite timestamps and larger
    future jumps are rejected instead of being made artificially fresh.
    """
    value = _as_float(report_time)
    if value is None or value <= 0:
        return None
    if value > 10_000_000_000:
        value /= 1000.0
    now = _as_float(time.time() if now_epoch is None else now_epoch)
    if now is None:
        return None
    age = now - value
    if age < -CLOUD_CLOCK_FUTURE_TOLERANCE_SECONDS:
        return None
    return max(0.0, age)


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
    timestamp is old or invalid, but is gate-usable only while that timestamp is
    recent and finite.
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
        maximum = _as_float(cloud_gate_max_age)
        gate_usable = bool(
            maximum is not None
            and maximum >= 0
            and cloud_age is not None
            and cloud_age <= maximum
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


def apply_docked_display_override(
    result: dict[str, Any],
    *,
    docked: bool,
    pending_activity: Any,
) -> bool:
    """Expose a confirmed docked mower as the virtual Dock physical area.

    Dock/charging state is stronger evidence for physical-area display than a
    stale, unavailable or boundary-flapping pose. A pending local mowing,
    pause or return command suppresses the override so the old docked flag
    cannot mask a mower that has just been dispatched.
    """
    if not docked or pending_activity is not None:
        return False

    result.update(
        {
            "current_physical_zone": "Dock",
            "current_physical_zone_id": None,
            "current_physical_zone_source": "docked_state",
            "current_physical_zone_position_source": "state",
            "current_physical_zone_position_age": None,
            "current_physical_zone_stale": False,
            "current_channel": "Not in channel",
            "current_channel_id": None,
            "current_channel_connection": [],
            "current_channel_distance": None,
            "current_channel_source": "docked_state",
            "current_channel_pose_age": None,
            "current_channel_stale": False,
            "current_channel_pose_valid": False,
        }
    )
    return True
