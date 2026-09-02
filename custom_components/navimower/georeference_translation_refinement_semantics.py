"""Refine static vendor map translation from trustworthy cloud XY/GPS fits.

Some map generations expose internally excellent static origin/center/bounds ties
while their common absolute translation disagrees with the mower's paired local
X/Y + GPS observations. Keep the vendor static map geometry (rotation and local
shape) authoritative and use a mature learned cloud fit only to refine the
translation component.

This is deliberately evidence-driven rather than model-name driven. X3 RTK
anchors are excluded because their vendor RTK/bias path already owns absolute
translation. The learned fit must pass tighter quality guards than the generic
fallback georeference before it is allowed to move a static map.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Callable

from . import georeference as _georeference

_STATIC_SOURCE = "vendor_map_static_fit"
_X3_SOURCE = "x3_rtk_anchor"
_GEOSY_MODEL = "wgs84_ellipsoid_v1"

_MIN_SAMPLE_COUNT = 8
_MIN_BASELINE_M = 15.0
_MAX_RMS_ERROR_M = 0.75
_MAX_POINT_ERROR_M = 1.5
_MIN_OBSERVED_SCALE = 0.98
_MAX_OBSERVED_SCALE = 1.02
_MAX_ROTATION_DIFFERENCE_DEG = 2.0
_MIN_TRANSLATION_M = 0.25
_MAX_TRANSLATION_M = 4.0

_INSTALLED = False
_ORIGINAL_UPDATE: Callable[[Any, Any], dict[str, Any] | None] | None = None


def _bearing(east_m: float, north_m: float) -> float:
    return (math.degrees(math.atan2(east_m, north_m)) + 360.0) % 360.0


def _rotation_difference_deg(first: Any, second: Any) -> float | None:
    first_rotation = _georeference._float(  # noqa: SLF001
        first.get("rotation_rad") if isinstance(first, dict) else None
    )
    second_rotation = _georeference._float(  # noqa: SLF001
        second.get("rotation_rad") if isinstance(second, dict) else None
    )
    if first_rotation is None or second_rotation is None:
        return None
    delta = (second_rotation - first_rotation + math.pi) % (2.0 * math.pi) - math.pi
    return abs(math.degrees(delta))


def _learned_fit(map_geometry: dict[str, Any]) -> dict[str, Any] | None:
    calibration = map_geometry.get("_georeference_calibration")
    if not isinstance(calibration, dict):
        return None
    fit = calibration.get("fit")
    if not isinstance(fit, dict) or not _georeference.georeference_is_valid(fit):
        return None
    return fit


def _quality_guard(
    map_geometry: dict[str, Any],
    static: dict[str, Any],
    learned: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    calibration_state = map_geometry.get("_georeference_calibration")
    calibration_state = calibration_state if isinstance(calibration_state, dict) else {}
    metrics = learned.get("calibration")
    metrics = metrics if isinstance(metrics, dict) else {}

    sample_count = int(metrics.get("inlier_count") or metrics.get("sample_count") or 0)
    baseline_m = _georeference._float(metrics.get("baseline_m"))  # noqa: SLF001
    rms_error_m = _georeference._float(metrics.get("rms_error_m"))  # noqa: SLF001
    max_error_m = _georeference._float(metrics.get("max_error_m"))  # noqa: SLF001
    observed_scale = _georeference._float(metrics.get("observed_scale"))  # noqa: SLF001
    rotation_difference_deg = _rotation_difference_deg(static, learned)
    geodesy_model = (
        learned.get("geodesy_model")
        or calibration_state.get("geodesy_model")
        or static.get("geodesy_model")
    )

    evidence = {
        "sample_count": sample_count,
        "baseline_m": baseline_m,
        "rms_error_m": rms_error_m,
        "max_error_m": max_error_m,
        "observed_scale": observed_scale,
        "rotation_difference_deg": (
            round(rotation_difference_deg, 3)
            if rotation_difference_deg is not None
            else None
        ),
        "geodesy_model": geodesy_model,
        "limits": {
            "min_sample_count": _MIN_SAMPLE_COUNT,
            "min_baseline_m": _MIN_BASELINE_M,
            "max_rms_error_m": _MAX_RMS_ERROR_M,
            "max_point_error_m": _MAX_POINT_ERROR_M,
            "observed_scale": [_MIN_OBSERVED_SCALE, _MAX_OBSERVED_SCALE],
            "max_rotation_difference_deg": _MAX_ROTATION_DIFFERENCE_DEG,
            "translation_m": [_MIN_TRANSLATION_M, _MAX_TRANSLATION_M],
        },
    }

    if geodesy_model != _GEOSY_MODEL:
        return False, "ellipsoid_geodesy_not_confirmed", evidence
    if sample_count < _MIN_SAMPLE_COUNT:
        return False, "insufficient_samples", evidence
    if baseline_m is None or baseline_m < _MIN_BASELINE_M:
        return False, "insufficient_baseline", evidence
    if rms_error_m is None or rms_error_m > _MAX_RMS_ERROR_M:
        return False, "rms_error_too_large", evidence
    if max_error_m is None or max_error_m > _MAX_POINT_ERROR_M:
        return False, "point_error_too_large", evidence
    if (
        observed_scale is None
        or not (_MIN_OBSERVED_SCALE <= observed_scale <= _MAX_OBSERVED_SCALE)
    ):
        return False, "observed_scale_outside_guard", evidence
    if (
        rotation_difference_deg is None
        or rotation_difference_deg > _MAX_ROTATION_DIFFERENCE_DEG
    ):
        return False, "rotation_disagreement_too_large", evidence
    return True, "quality_guard_passed", evidence


def _translation_at_learned_reference(
    static: dict[str, Any], learned: dict[str, Any]
) -> tuple[float, float, float, float] | None:
    learned_reference = learned.get("reference") or {}
    anchor_x = _georeference._float(learned_reference.get("local_x"))  # noqa: SLF001
    anchor_y = _georeference._float(learned_reference.get("local_y"))  # noqa: SLF001
    if anchor_x is None or anchor_y is None:
        return None

    static_point = _georeference.local_xy_to_wgs84(static, anchor_x, anchor_y)
    learned_point = _georeference.local_xy_to_wgs84(learned, anchor_x, anchor_y)
    if static_point is None or learned_point is None:
        return None

    offset = _georeference.wgs84_offset_m(
        static_point[0],
        static_point[1],
        learned_point[0],
        learned_point[1],
    )
    if offset is None:
        return None
    east_m, north_m = offset
    return anchor_x, anchor_y, east_m, north_m


def _refine_static_translation(
    map_geometry: dict[str, Any],
    active: dict[str, Any],
    location: Any,
) -> dict[str, Any]:
    if active.get("source") == _X3_SOURCE:
        return active
    if active.get("source") != _STATIC_SOURCE:
        return active

    learned = _learned_fit(map_geometry)
    if learned is None:
        return active

    allowed, reason, evidence = _quality_guard(map_geometry, active, learned)
    updated = deepcopy(active)
    metadata: dict[str, Any] = {
        "candidate_source": "cloud_location_fit",
        "translation_only": True,
        "vendor_rotation_preserved": True,
        "quality": evidence,
    }
    if not allowed:
        metadata.update({"applied": False, "reason": reason})
        updated["translation_refinement"] = metadata
        map_geometry["georeference"] = updated
        return updated

    translation = _translation_at_learned_reference(active, learned)
    if translation is None:
        metadata.update({"applied": False, "reason": "translation_projection_failed"})
        updated["translation_refinement"] = metadata
        map_geometry["georeference"] = updated
        return updated

    anchor_x, anchor_y, east_m, north_m = translation
    distance_m = math.hypot(east_m, north_m)
    if distance_m < _MIN_TRANSLATION_M:
        metadata.update(
            {
                "applied": False,
                "reason": "difference_below_refinement_threshold",
                "east_m": round(east_m, 3),
                "north_m": round(north_m, 3),
                "distance_m": round(distance_m, 3),
                "bearing_deg_from_north": round(_bearing(east_m, north_m), 2),
            }
        )
        updated["translation_refinement"] = metadata
        map_geometry["georeference"] = updated
        return updated
    if distance_m > _MAX_TRANSLATION_M:
        metadata.update(
            {
                "applied": False,
                "reason": "translation_exceeds_guard",
                "east_m": round(east_m, 3),
                "north_m": round(north_m, 3),
                "distance_m": round(distance_m, 3),
                "bearing_deg_from_north": round(_bearing(east_m, north_m), 2),
            }
        )
        updated["translation_refinement"] = metadata
        map_geometry["georeference"] = updated
        return updated

    reference = active.get("reference") or {}
    latitude = _georeference._float(reference.get("latitude"))  # noqa: SLF001
    longitude = _georeference._float(reference.get("longitude"))  # noqa: SLF001
    if latitude is None or longitude is None:
        metadata.update({"applied": False, "reason": "static_reference_missing"})
        updated["translation_refinement"] = metadata
        map_geometry["georeference"] = updated
        return updated

    corrected = _georeference.offset_wgs84(
        latitude,
        longitude,
        east_m,
        north_m,
    )
    if corrected is None:
        metadata.update({"applied": False, "reason": "reference_offset_failed"})
        updated["translation_refinement"] = metadata
        map_geometry["georeference"] = updated
        return updated

    corrected_reference = dict(reference)
    corrected_reference["latitude"] = corrected[0]
    corrected_reference["longitude"] = corrected[1]
    updated["reference"] = corrected_reference
    updated["anchor_policy"] = "static_map_geometry_cloud_translation_refined"
    metadata.update(
        {
            "applied": True,
            "reason": "mature_cloud_fit_translation",
            "anchor_local_x": round(anchor_x, 3),
            "anchor_local_y": round(anchor_y, 3),
            "east_m": round(east_m, 3),
            "north_m": round(north_m, 3),
            "distance_m": round(distance_m, 3),
            "bearing_deg_from_north": round(_bearing(east_m, north_m), 2),
        }
    )
    updated["translation_refinement"] = metadata

    # Validate the refined vendor-geometry transform in the same raw vendor GPS
    # frame before the later ETRS89 presentation translation is stacked on top.
    checked = _georeference.validate_georeference(
        updated,
        location,
        limit_m=_MAX_POINT_ERROR_M,
    )
    if isinstance(checked, dict):
        updated["cloud_validation"] = dict(checked.get("validation") or {})

    map_geometry["georeference"] = updated
    return updated


def _update(map_geometry: Any, location: Any) -> dict[str, Any] | None:
    if _ORIGINAL_UPDATE is None:
        return None
    active = _ORIGINAL_UPDATE(map_geometry, location)
    if not isinstance(map_geometry, dict) or not isinstance(active, dict):
        return active
    return _refine_static_translation(map_geometry, active, location)


def install_georeference_translation_refinement_semantics() -> None:
    """Install guarded translation refinement after static/X3 vendor semantics."""
    global _INSTALLED, _ORIGINAL_UPDATE
    if _INSTALLED:
        return

    from . import coordinator_semantics as _coordinator_semantics

    _ORIGINAL_UPDATE = _georeference.update_georeference
    _georeference.update_georeference = _update
    _coordinator_semantics.update_georeference = _update
    _INSTALLED = True


__all__ = ["install_georeference_translation_refinement_semantics"]
