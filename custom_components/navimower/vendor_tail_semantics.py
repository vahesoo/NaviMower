"""Compatibility adapter for unbounded, vendor-anchored MQTT tails."""
from __future__ import annotations
from typing import Any
from . import coordinator_semantics

_INSTALLED = False
_ORIGINAL_TRIM = coordinator_semantics.trim_mqtt_tail_segments


def _strict_trim_mqtt_tail_segments(segments, vendor_points, *, radius_m=0.75) -> tuple[list, dict[str, Any]]:
    tail, metrics = _ORIGINAL_TRIM(segments, vendor_points, radius_m=radius_m)
    metrics = dict(metrics or {})
    metrics["tail_limit_m"] = None
    metrics["suppressed_without_vendor_anchor"] = bool(vendor_points and not metrics.get("matched"))
    if metrics["suppressed_without_vendor_anchor"]:
        metrics["mqtt_tail_point_count"] = 0
        metrics["mqtt_tail_distance_m"] = 0.0
        return [], metrics
    return tail, metrics


def install_vendor_tail_semantics() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    coordinator_semantics.trim_mqtt_tail_segments = _strict_trim_mqtt_tail_segments
    _INSTALLED = True
