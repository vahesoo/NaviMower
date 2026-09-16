"""Strict vendor-backbone / short-MQTT-tail semantics.

When retained vendor geometry exists, MQTT is only a live extension ahead of the
vendor trail. If the vendor endpoint cannot be anchored into the MQTT history,
fail closed and show vendor geometry alone instead of resurrecting the full
Home Assistant session trail. Successful tails are capped to a short distance so
Map Card rendering stays visually stable while the vendor backbone catches up.
"""
from __future__ import annotations

import math
from typing import Any

from . import coordinator_semantics

_INSTALLED = False
_MAX_TAIL_DISTANCE_M = 8.0
_ORIGINAL_TRIM = coordinator_semantics.trim_mqtt_tail_segments


def _distance(a: list[float], b: list[float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _trim_segment_tail(segment: list[list[float]], limit_m: float) -> tuple[list[list[float]], float]:
    """Keep at most ``limit_m`` from the end of one continuous segment."""
    if not segment or limit_m <= 0:
        return [], 0.0
    if len(segment) == 1:
        return [list(segment[-1])], 0.0

    kept: list[list[float]] = [list(segment[-1])]
    used = 0.0
    for index in range(len(segment) - 2, -1, -1):
        older = list(segment[index])
        newer = kept[0]
        step = _distance(older, newer)
        if step <= 0:
            kept.insert(0, older)
            continue
        remaining = limit_m - used
        if remaining <= 0:
            break
        if step <= remaining:
            kept.insert(0, older)
            used += step
            continue
        ratio = remaining / step
        boundary = [
            newer[0] + (older[0] - newer[0]) * ratio,
            newer[1] + (older[1] - newer[1]) * ratio,
        ]
        kept.insert(0, boundary)
        used = limit_m
        break
    return kept, used


def _short_tail(segments: list[list[list[float]]], limit_m: float) -> tuple[list[list[list[float]]], float]:
    """Keep the newest continuous tail without bridging segment gaps."""
    if not segments:
        return [], 0.0
    newest = segments[-1]
    kept, used = _trim_segment_tail(newest, limit_m)
    return ([kept] if kept else []), used


def _strict_trim_mqtt_tail_segments(
    segments: list[list[list[float]]],
    vendor_points: list[list[Any]],
    *,
    radius_m: float = 0.75,
) -> tuple[list[list[list[float]]], dict[str, Any]]:
    tail, metrics = _ORIGINAL_TRIM(segments, vendor_points, radius_m=radius_m)
    result_metrics = dict(metrics or {})

    if vendor_points and not result_metrics.get("matched"):
        result_metrics["mqtt_tail_point_count"] = 0
        result_metrics["mqtt_tail_distance_m"] = 0.0
        result_metrics["suppressed_without_vendor_anchor"] = True
        result_metrics["tail_limit_m"] = _MAX_TAIL_DISTANCE_M
        return [], result_metrics

    short, distance = _short_tail(tail, _MAX_TAIL_DISTANCE_M)
    result_metrics["mqtt_tail_point_count"] = sum(len(item) for item in short)
    result_metrics["mqtt_tail_distance_m"] = round(distance, 2)
    result_metrics["suppressed_without_vendor_anchor"] = False
    result_metrics["tail_limit_m"] = _MAX_TAIL_DISTANCE_M
    return short, result_metrics


def install_vendor_tail_semantics() -> None:
    """Install strict retained-vendor/live-MQTT arbitration once."""
    global _INSTALLED
    if _INSTALLED:
        return
    coordinator_semantics.trim_mqtt_tail_segments = _strict_trim_mqtt_tail_segments
    _INSTALLED = True


__all__ = ["install_vendor_tail_semantics"]
