"""Provider-ready geographic reference frames for map underlays.

The mower map itself always stays in one local X/Y frame.  Underlay providers,
however, can be registered to different geographic reference frames.  Resolve
those differences in the integration so the frontend never needs model-specific
or datum-specific geodesy.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from . import georeference as _georeference
from .map_underlay import coordinator_country_code

FRAME_ACTIVE = "active"
FRAME_WEB_WGS84 = "web_wgs84"
FRAME_REGIONAL_CARTOGRAPHIC = "regional_cartographic"

_GEOSY_MODEL = "wgs84_ellipsoid_v1"
_MIN_SAMPLE_COUNT = 8
_MIN_BASELINE_M = 15.0
_MAX_RMS_ERROR_M = 0.75
_MAX_POINT_ERROR_M = 1.5
_MIN_OBSERVED_SCALE = 0.98
_MAX_OBSERVED_SCALE = 1.02
_MAX_ROTATION_DIFFERENCE_DEG = 2.0
_MAX_WEB_TRANSLATION_M = 10.0


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _bearing(east_m: float, north_m: float) -> float:
    return (math.degrees(math.atan2(east_m, north_m)) + 360.0) % 360.0


def _active_georeference(coordinator: Any) -> dict[str, Any] | None:
    data = getattr(coordinator, "data", None) or {}
    active = data.get("georeference")
    if not isinstance(active, dict):
        map_data = data.get("map") if isinstance(data.get("map"), dict) else {}
        active = map_data.get("georeference")
    if not _georeference.georeference_is_valid(active):
        return None
    return active


def _offset_summary(east_m: float, north_m: float) -> dict[str, float]:
    distance_m = math.hypot(east_m, north_m)
    return {
        "east_m": round(east_m, 3),
        "north_m": round(north_m, 3),
        "distance_m": round(distance_m, 3),
        "bearing_deg_from_north": round(_bearing(east_m, north_m), 2),
    }


def _frame(
    *,
    available: bool,
    source: str,
    georeference: dict[str, Any] | None = None,
    offset_from_active: dict[str, Any] | None = None,
    reason: str | None = None,
    quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": bool(available),
        "source": source,
    }
    if reason:
        result["reason"] = reason
    if offset_from_active is not None:
        result["offset_from_active"] = deepcopy(offset_from_active)
    if quality is not None:
        result["quality"] = deepcopy(quality)
    if georeference is not None:
        result["georeference"] = deepcopy(georeference)
    return result


def _rotation_difference_deg(first: Any, second: Any) -> float | None:
    first_rotation = _float(first.get("rotation_rad") if isinstance(first, dict) else None)
    second_rotation = _float(second.get("rotation_rad") if isinstance(second, dict) else None)
    if first_rotation is None or second_rotation is None:
        return None
    delta = (second_rotation - first_rotation + math.pi) % (2.0 * math.pi) - math.pi
    return abs(math.degrees(delta))


def _learned_fit(coordinator: Any) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    geometry = getattr(coordinator, "_map_geometry", None)
    geometry = geometry if isinstance(geometry, dict) else {}
    calibration = geometry.get("_georeference_calibration")
    calibration = calibration if isinstance(calibration, dict) else {}
    fit = calibration.get("fit")
    if not isinstance(fit, dict) or not _georeference.georeference_is_valid(fit):
        return None, calibration
    return fit, calibration


def _learned_quality(
    active: dict[str, Any],
    learned: dict[str, Any],
    calibration_state: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    metrics = learned.get("calibration")
    metrics = metrics if isinstance(metrics, dict) else {}
    sample_count = int(metrics.get("inlier_count") or metrics.get("sample_count") or 0)
    baseline_m = _float(metrics.get("baseline_m"))
    rms_error_m = _float(metrics.get("rms_error_m"))
    max_error_m = _float(metrics.get("max_error_m"))
    observed_scale = _float(metrics.get("observed_scale"))
    rotation_difference = _rotation_difference_deg(active, learned)
    geodesy_model = (
        learned.get("geodesy_model")
        or calibration_state.get("geodesy_model")
        or active.get("geodesy_model")
    )
    quality = {
        "sample_count": sample_count,
        "baseline_m": baseline_m,
        "rms_error_m": rms_error_m,
        "max_error_m": max_error_m,
        "observed_scale": observed_scale,
        "rotation_difference_deg": (
            round(rotation_difference, 3) if rotation_difference is not None else None
        ),
        "geodesy_model": geodesy_model,
        "translation_only": True,
        "limits": {
            "min_sample_count": _MIN_SAMPLE_COUNT,
            "min_baseline_m": _MIN_BASELINE_M,
            "max_rms_error_m": _MAX_RMS_ERROR_M,
            "max_point_error_m": _MAX_POINT_ERROR_M,
            "observed_scale": [_MIN_OBSERVED_SCALE, _MAX_OBSERVED_SCALE],
            "max_rotation_difference_deg": _MAX_ROTATION_DIFFERENCE_DEG,
            "max_translation_m": _MAX_WEB_TRANSLATION_M,
        },
    }
    if geodesy_model != _GEOSY_MODEL:
        return False, "ellipsoid_geodesy_not_confirmed", quality
    if sample_count < _MIN_SAMPLE_COUNT:
        return False, "insufficient_samples", quality
    if baseline_m is None or baseline_m < _MIN_BASELINE_M:
        return False, "insufficient_baseline", quality
    if rms_error_m is None or rms_error_m > _MAX_RMS_ERROR_M:
        return False, "rms_error_too_large", quality
    if max_error_m is None or max_error_m > _MAX_POINT_ERROR_M:
        return False, "point_error_too_large", quality
    if observed_scale is None or not (_MIN_OBSERVED_SCALE <= observed_scale <= _MAX_OBSERVED_SCALE):
        return False, "observed_scale_outside_guard", quality
    if rotation_difference is None or rotation_difference > _MAX_ROTATION_DIFFERENCE_DEG:
        return False, "rotation_disagreement_too_large", quality
    return True, "quality_guard_passed", quality


def _translate_reference(
    active: dict[str, Any], east_m: float, north_m: float
) -> dict[str, Any] | None:
    reference = active.get("reference") or {}
    latitude = _float(reference.get("latitude"))
    longitude = _float(reference.get("longitude"))
    if latitude is None or longitude is None:
        return None
    corrected = _georeference.offset_wgs84(latitude, longitude, east_m, north_m)
    if corrected is None:
        return None
    result = deepcopy(active)
    result_reference = dict(reference)
    result_reference["latitude"] = corrected[0]
    result_reference["longitude"] = corrected[1]
    result["reference"] = result_reference
    return result


def _web_from_inverse_cartographic(active: dict[str, Any]) -> dict[str, Any] | None:
    frame = active.get("cartographic_frame")
    if not isinstance(frame, dict) or frame.get("applied") is not True:
        return None
    east_m = _float(frame.get("east_m"))
    north_m = _float(frame.get("north_m"))
    if east_m is None or north_m is None:
        return None
    translated = _translate_reference(active, -east_m, -north_m)
    if translated is None:
        return None
    translated["provider_frame"] = {
        "name": FRAME_WEB_WGS84,
        "source": "inverse_active_cartographic_translation",
        "translation_only": True,
    }
    return _frame(
        available=True,
        source="inverse_active_cartographic_translation",
        georeference=translated,
        offset_from_active=_offset_summary(-east_m, -north_m),
    )


def _web_from_learned_translation(
    coordinator: Any,
    active: dict[str, Any],
) -> dict[str, Any]:
    learned, calibration_state = _learned_fit(coordinator)
    if learned is None:
        return _frame(
            available=False,
            source="cloud_translation_fit",
            reason="learned_fit_unavailable",
        )
    allowed, reason, quality = _learned_quality(active, learned, calibration_state)
    if not allowed:
        return _frame(
            available=False,
            source="cloud_translation_fit",
            reason=reason,
            quality=quality,
        )

    reference = active.get("reference") or {}
    local_x = _float(reference.get("local_x"))
    local_y = _float(reference.get("local_y"))
    active_lat = _float(reference.get("latitude"))
    active_lon = _float(reference.get("longitude"))
    if None in (local_x, local_y, active_lat, active_lon):
        return _frame(
            available=False,
            source="cloud_translation_fit",
            reason="active_reference_incomplete",
            quality=quality,
        )
    target = _georeference.local_xy_to_wgs84(learned, local_x, local_y)
    if target is None:
        return _frame(
            available=False,
            source="cloud_translation_fit",
            reason="learned_projection_failed",
            quality=quality,
        )
    offset = _georeference.wgs84_offset_m(active_lat, active_lon, target[0], target[1])
    if offset is None:
        return _frame(
            available=False,
            source="cloud_translation_fit",
            reason="translation_measurement_failed",
            quality=quality,
        )
    east_m, north_m = offset
    distance_m = math.hypot(east_m, north_m)
    if distance_m > _MAX_WEB_TRANSLATION_M:
        quality = {**quality, "measured_translation_m": round(distance_m, 3)}
        return _frame(
            available=False,
            source="cloud_translation_fit",
            reason="translation_exceeds_guard",
            quality=quality,
            offset_from_active=_offset_summary(east_m, north_m),
        )

    result = deepcopy(active)
    result_reference = dict(reference)
    result_reference["latitude"] = target[0]
    result_reference["longitude"] = target[1]
    result["reference"] = result_reference
    result["provider_frame"] = {
        "name": FRAME_WEB_WGS84,
        "source": "cloud_translation_fit",
        "translation_only": True,
        "active_rotation_preserved": True,
    }
    return _frame(
        available=True,
        source="cloud_translation_fit",
        georeference=result,
        offset_from_active=_offset_summary(east_m, north_m),
        quality=quality,
    )


def _vendor_regional_translation_owner(active: dict[str, Any]) -> bool:
    frame = active.get("cartographic_frame")
    if not isinstance(frame, dict) or frame.get("applied") is not False:
        return False
    reason = str(frame.get("reason") or "")
    return "vendor_rtk_frame_owns_translation" in reason


def build_georeference_frames(coordinator: Any) -> dict[str, Any]:
    """Return provider-ready frames without changing mower/local-map geometry."""
    active = _active_georeference(coordinator)
    if active is None:
        unavailable = _frame(available=False, source="unavailable", reason="active_georeference_unavailable")
        return {
            FRAME_ACTIVE: unavailable,
            FRAME_WEB_WGS84: deepcopy(unavailable),
            FRAME_REGIONAL_CARTOGRAPHIC: deepcopy(unavailable),
        }

    zero = _offset_summary(0.0, 0.0)
    frames: dict[str, Any] = {
        FRAME_ACTIVE: _frame(
            available=True,
            source="active",
            georeference=active,
            offset_from_active=zero,
        )
    }

    inverse = _web_from_inverse_cartographic(active)
    if inverse is not None:
        frames[FRAME_WEB_WGS84] = inverse
    elif _vendor_regional_translation_owner(active):
        frames[FRAME_WEB_WGS84] = _web_from_learned_translation(coordinator, active)
    else:
        web = deepcopy(active)
        web["provider_frame"] = {
            "name": FRAME_WEB_WGS84,
            "source": "active",
            "translation_only": True,
        }
        frames[FRAME_WEB_WGS84] = _frame(
            available=True,
            source="active",
            georeference=web,
            offset_from_active=zero,
        )

    country_code = coordinator_country_code(coordinator)
    cartographic = active.get("cartographic_frame")
    cartographic_applied = isinstance(cartographic, dict) and cartographic.get("applied") is True
    vendor_regional = _vendor_regional_translation_owner(active)
    if country_code == "EE" and (cartographic_applied or vendor_regional):
        regional = deepcopy(active)
        regional["provider_frame"] = {
            "name": FRAME_REGIONAL_CARTOGRAPHIC,
            "source": "active_cartographic" if cartographic_applied else "vendor_rtk_regional",
            "translation_only": True,
        }
        frames[FRAME_REGIONAL_CARTOGRAPHIC] = _frame(
            available=True,
            source="active_cartographic" if cartographic_applied else "vendor_rtk_regional",
            georeference=regional,
            offset_from_active=zero,
        )
    elif country_code == "EE":
        frames[FRAME_REGIONAL_CARTOGRAPHIC] = _frame(
            available=False,
            source="active_fallback",
            reason="regional_frame_not_confirmed",
            offset_from_active=zero,
        )
    else:
        frames[FRAME_REGIONAL_CARTOGRAPHIC] = _frame(
            available=False,
            source="unavailable",
            reason="no_regional_cartographic_provider",
        )
    return frames


def georeference_frame_diagnostics(coordinator: Any) -> dict[str, Any]:
    """Return frame status/offset evidence without geographic coordinates."""
    result: dict[str, Any] = {}
    for name, frame in build_georeference_frames(coordinator).items():
        result[name] = {
            key: deepcopy(frame.get(key))
            for key in ("available", "source", "reason", "offset_from_active", "quality")
            if key in frame
        }
    return result


def site_underlay_origins(origin: Any, coordinator: Any) -> dict[str, Any]:
    """Translate one active Multi-mower site origin into each provider frame."""
    if not isinstance(origin, dict):
        return {}
    latitude = _float(origin.get("latitude"))
    longitude = _float(origin.get("longitude"))
    if latitude is None or longitude is None:
        return {}

    result: dict[str, Any] = {}
    for name, frame in build_georeference_frames(coordinator).items():
        if frame.get("available") is not True:
            result[name] = {
                "available": False,
                "source": frame.get("source"),
                "reason": frame.get("reason"),
            }
            continue
        offset = frame.get("offset_from_active") or {}
        east_m = _float(offset.get("east_m")) or 0.0
        north_m = _float(offset.get("north_m")) or 0.0
        translated = _georeference.offset_wgs84(latitude, longitude, east_m, north_m)
        if translated is None:
            result[name] = {
                "available": False,
                "source": frame.get("source"),
                "reason": "site_origin_translation_failed",
            }
            continue
        result[name] = {
            "available": True,
            "source": frame.get("source"),
            "latitude": translated[0],
            "longitude": translated[1],
        }
    return result


__all__ = [
    "FRAME_ACTIVE",
    "FRAME_REGIONAL_CARTOGRAPHIC",
    "FRAME_WEB_WGS84",
    "build_georeference_frames",
    "georeference_frame_diagnostics",
    "site_underlay_origins",
]
