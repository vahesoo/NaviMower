"""Apply validated X3 NRTK-to-LRTK bias to RTK-anchored map references."""
from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any, Callable

from . import georeference as _georeference
from . import georeference_static_anchor_semantics as _static

_X3_RTK_SOURCE = "x3_rtk_anchor"
_PROBE_MARKER = "x3_rtk_bias_v1"
_MAX_BIAS_STD_M = 0.25
_MAX_HORIZONTAL_BIAS_M = 25.0
_FLOAT_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"

_INSTALLED = False
_ORIGINAL_APPLY: Callable[[dict[str, Any], dict[str, Any], tuple[float, float]], dict[str, Any]] | None = None
_ORIGINAL_UPDATE: Callable[[Any, Any], dict[str, Any] | None] | None = None
_ORIGINAL_LOAD: Callable[..., Any] | None = None


def _three_floats(raw: str, key: str) -> list[float] | None:
    match = re.search(
        rf"{re.escape(key)}:\s*({_FLOAT_RE})\s+({_FLOAT_RE})\s+({_FLOAT_RE})",
        raw,
    )
    if match is None:
        return None
    values = [float(match.group(index)) for index in range(1, 4)]
    return values if all(math.isfinite(value) for value in values) else None


def _x3_bias_metadata(geom: dict[str, Any]) -> dict[str, Any] | None:
    """Parse X3 NRTK/LRTK calibration metadata and evaluate its quality."""
    rtk = geom.get("rtk")
    raw = rtk.get("bias") if isinstance(rtk, dict) else None
    if not isinstance(raw, str):
        return None

    flag_match = re.search(
        r"nrtk_lrtk_calibration_flag:\s*(true|false|1|0)",
        raw,
        flags=re.IGNORECASE,
    )
    refined_match = re.search(r"nrtk_lrtk_bias_refined:\s*(\d+)", raw)
    bias = _three_floats(raw, "nrtk_lrtk_bias")
    std = _three_floats(raw, "nrtk_lrtk_bias_std")

    calibration_flag = (
        flag_match is not None
        and flag_match.group(1).lower() in {"true", "1"}
    )
    refined = refined_match is not None and refined_match.group(1) == "1"
    horizontal_m = math.hypot(bias[0], bias[1]) if bias is not None else None
    std_ok = (
        std is not None
        and all(0.0 <= value <= _MAX_BIAS_STD_M for value in std)
    )
    magnitude_ok = horizontal_m is not None and horizontal_m <= _MAX_HORIZONTAL_BIAS_M
    usable = bool(calibration_flag and refined and bias is not None and std_ok and magnitude_ok)

    reasons: list[str] = []
    if not calibration_flag:
        reasons.append("calibration_not_confirmed")
    if not refined:
        reasons.append("bias_not_refined")
    if bias is None:
        reasons.append("bias_missing_or_invalid")
    if std is None:
        reasons.append("bias_std_missing_or_invalid")
    elif not std_ok:
        reasons.append("bias_std_too_large")
    if horizontal_m is not None and not magnitude_ok:
        reasons.append("horizontal_bias_too_large")

    return {
        "calibration_flag": calibration_flag,
        "refined": refined,
        "nrtk_lrtk_bias": bias,
        "nrtk_lrtk_bias_std": std,
        "horizontal_m": round(horizontal_m, 3) if horizontal_m is not None else None,
        "std_limit_m": _MAX_BIAS_STD_M,
        "horizontal_limit_m": _MAX_HORIZONTAL_BIAS_M,
        "usable": usable,
        "reasons": reasons,
    }


def _difference(
    origin: tuple[float, float], target: tuple[float, float]
) -> dict[str, float] | None:
    offset = _georeference.wgs84_offset_m(origin[0], origin[1], target[0], target[1])
    if offset is None:
        return None
    east_m, north_m = offset
    return {
        "east_m": round(east_m, 3),
        "north_m": round(north_m, 3),
        "distance_m": round(math.hypot(east_m, north_m), 3),
        "bearing_deg_from_north": round(
            (math.degrees(math.atan2(east_m, north_m)) + 360.0) % 360.0,
            2,
        ),
    }


def _apply_x3_rtk_bias(
    geom: dict[str, Any], candidate: dict[str, Any], origin: tuple[float, float]
) -> dict[str, Any]:
    """Apply the refined vendor NRTK/LRTK horizontal bias to an X3 RTK anchor.

    X390 field data shows the vendor bias plane uses the same Y/X ordering seen
    in the LRTK pile record. Therefore raw bias[0] is interpreted as north and
    raw bias[1] as east. The correction is applied in full, in metres, on top of
    ``RTK_anchor`` while the static vendor tie fit continues to own rotation.
    """
    if _ORIGINAL_APPLY is None:
        return candidate
    result = _ORIGINAL_APPLY(geom, candidate, origin)
    if result.get("source") != _X3_RTK_SOURCE:
        return result

    metadata = _x3_bias_metadata(geom)
    validation = result.get("rtk_validation")
    if not isinstance(validation, dict):
        return result

    updated = deepcopy(result)
    updated_validation = updated["rtk_validation"]
    if metadata is None:
        updated_validation["bias_correction"] = {
            "applied": False,
            "reason": "bias_metadata_missing",
        }
        return updated

    updated_validation["bias"] = metadata
    if metadata.get("usable") is not True:
        updated_validation["bias_correction"] = {
            "applied": False,
            "reason": "bias_quality_guard",
            "reasons": list(metadata.get("reasons") or []),
        }
        return updated

    bias = metadata.get("nrtk_lrtk_bias")
    if not isinstance(bias, list) or len(bias) != 3:
        return updated

    correction_north_m = float(bias[0])
    correction_east_m = float(bias[1])
    corrected = _georeference.offset_wgs84(
        updated["rtk_anchor"]["latitude"],
        updated["rtk_anchor"]["longitude"],
        correction_east_m,
        correction_north_m,
    )
    if corrected is None:
        updated_validation["bias_correction"] = {
            "applied": False,
            "reason": "wgs84_offset_failed",
        }
        return updated

    corrected_latitude, corrected_longitude = corrected
    updated["anchor_policy"] = "x3_rtk_anchor_bias_primary"
    updated["reference"] = {
        "local_x": 0.0,
        "local_y": 0.0,
        "latitude": corrected_latitude,
        "longitude": corrected_longitude,
    }
    updated_validation["translation_source"] = "RTK_anchor+nrtk_lrtk_bias"
    updated_validation["bias_correction"] = {
        "applied": True,
        "axis_mapping": "east=bias[1], north=bias[0]",
        "east_m": round(correction_east_m, 3),
        "north_m": round(correction_north_m, 3),
        "distance_m": round(math.hypot(correction_east_m, correction_north_m), 3),
        "bearing_deg_from_north": round(
            (
                math.degrees(
                    math.atan2(correction_east_m, correction_north_m)
                )
                + 360.0
            )
            % 360.0,
            2,
        ),
    }
    active_difference = _difference(origin, corrected)
    if active_difference is not None:
        updated_validation["active_reference_difference"] = active_difference
    return updated


def _update(map_geometry: Any, location: Any) -> dict[str, Any] | None:
    if _ORIGINAL_UPDATE is None:
        return None
    result = _ORIGINAL_UPDATE(map_geometry, location)
    if isinstance(map_geometry, dict):
        map_geometry[_PROBE_MARKER] = True
    return result


async def _load_persistent_state(self: Any) -> None:
    """Refresh beta13 X3 caches once so the active reference gains the bias."""
    if _ORIGINAL_LOAD is None:
        return
    await _ORIGINAL_LOAD(self)
    geometry = getattr(self, "_map_geometry", None)
    if not isinstance(geometry, dict) or geometry.get(_PROBE_MARKER):
        return
    georeference = geometry.get("georeference")
    source = georeference.get("source") if isinstance(georeference, dict) else None
    if source == _X3_RTK_SOURCE:
        self._map_cache_key = None  # noqa: SLF001


def install_georeference_x3_bias_semantics() -> None:
    """Install X3 bias correction after RTK anchoring and before diagnostics."""
    global _INSTALLED, _ORIGINAL_APPLY, _ORIGINAL_UPDATE, _ORIGINAL_LOAD
    if _INSTALLED:
        return

    from . import coordinator_semantics as _coordinator_semantics

    _ORIGINAL_APPLY = _static._apply_x3_rtk_anchor  # noqa: SLF001
    _ORIGINAL_UPDATE = _georeference.update_georeference
    _ORIGINAL_LOAD = _coordinator_semantics.NavimowCoordinator.async_load_persistent_state

    _static._apply_x3_rtk_anchor = _apply_x3_rtk_bias  # noqa: SLF001
    _georeference.update_georeference = _update
    _coordinator_semantics.update_georeference = _update
    _coordinator_semantics.NavimowCoordinator.async_load_persistent_state = _load_persistent_state
    _INSTALLED = True


__all__ = ["install_georeference_x3_bias_semantics"]
