"""Normalize absolute-reference diagnostics into the active cartographic frame.

The reference-candidate collector compares raw vendor/cloud GPS points with the
active map reference. Once the active map has received the European ETRS89
presentation translation, those raw candidates must receive the same translation
before their residual is interpreted. Otherwise diagnostics artificially include
the common cartographic shift in every candidate-minus-active vector.
"""
from __future__ import annotations

import math
from typing import Any, Callable

from . import georeference_reference_diagnostics as _reference

_INSTALLED = False
_ORIGINAL_REFERENCE_DIAGNOSTICS: Callable[..., dict[str, Any]] | None = None


def _bearing(east_m: float, north_m: float) -> float:
    return (math.degrees(math.atan2(east_m, north_m)) + 360.0) % 360.0


def _normalized_reference_candidate_diagnostics(
    geometry: Any,
    active: Any,
    location: Any = None,
    *,
    docked: bool = False,
) -> dict[str, Any]:
    if _ORIGINAL_REFERENCE_DIAGNOSTICS is None:
        return {"read_only": True, "available": False}

    result = _ORIGINAL_REFERENCE_DIAGNOSTICS(
        geometry,
        active,
        location,
        docked=docked,
    )
    if not isinstance(result, dict):
        return result

    frame = result.get("cartographic_frame")
    frame = frame if isinstance(frame, dict) else {}
    if frame.get("applied") is not True:
        result["candidate_cartographic_normalization"] = {
            "applied": False,
            "reason": "active_cartographic_translation_not_applied",
        }
        return result

    try:
        frame_east = float(frame.get("east_m"))
        frame_north = float(frame.get("north_m"))
    except (TypeError, ValueError):
        result["candidate_cartographic_normalization"] = {
            "applied": False,
            "reason": "active_cartographic_translation_missing",
        }
        return result
    if not math.isfinite(frame_east) or not math.isfinite(frame_north):
        result["candidate_cartographic_normalization"] = {
            "applied": False,
            "reason": "active_cartographic_translation_invalid",
        }
        return result

    candidates = result.get("candidate_offsets")
    candidates = candidates if isinstance(candidates, dict) else {}
    normalized_count = 0
    for candidate in candidates.values():
        if not isinstance(candidate, dict):
            continue
        delta = candidate.get("candidate_minus_active")
        if not isinstance(delta, dict):
            continue
        try:
            east = float(delta.get("east_m")) + frame_east
            north = float(delta.get("north_m")) + frame_north
        except (TypeError, ValueError):
            continue
        if not math.isfinite(east) or not math.isfinite(north):
            continue
        delta.update(
            {
                "east_m": round(east, 3),
                "north_m": round(north, 3),
                "distance_m": round(math.hypot(east, north), 3),
                "bearing_deg_from_north": round(_bearing(east, north), 2),
            }
        )
        candidate["comparison_frame"] = "active_cartographic_frame"
        normalized_count += 1

    result["candidate_cartographic_normalization"] = {
        "applied": True,
        "method": "add_active_translation_to_raw_candidate",
        "east_m": round(frame_east, 3),
        "north_m": round(frame_north, 3),
        "candidate_count": normalized_count,
    }
    result["convention"] = (
        "candidate_minus_active in active cartographic frame; east/north metres; "
        "bearing clockwise from north"
    )
    return result


def install_georeference_diagnostics_frame_semantics() -> None:
    """Patch candidate diagnostics after cartographic semantics are installed."""
    global _INSTALLED, _ORIGINAL_REFERENCE_DIAGNOSTICS
    if _INSTALLED:
        return

    from . import georeference_diagnostics_semantics as _diagnostics

    _ORIGINAL_REFERENCE_DIAGNOSTICS = _reference.reference_candidate_diagnostics
    _reference.reference_candidate_diagnostics = _normalized_reference_candidate_diagnostics
    # georeference_diagnostics_semantics imports the function directly at module
    # import time, so update that bound reference as well.
    _diagnostics.reference_candidate_diagnostics = _normalized_reference_candidate_diagnostics
    _INSTALLED = True


__all__ = ["install_georeference_diagnostics_frame_semantics"]
