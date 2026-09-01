"""Prefer stable vendor map anchors, including X3 RTK origin metadata."""
from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any, Callable

from . import georeference as _georeference

_STATIC_SOURCE = "vendor_map_static_fit"
_X3_RTK_SOURCE = "x3_rtk_anchor"
_STATIC_MAX_POINT_ERROR_M = 1.0
_STATIC_MIN_DIAGONAL_M = 10.0
_STATIC_MIN_SCALE = 0.97
_STATIC_MAX_SCALE = 1.03
_PROBE_MARKER = "x3_rtk_anchor_v2"

_INSTALLED = False
_ORIGINAL_FROM_GEOMETRY: Callable[[Any], dict[str, Any] | None] | None = None
_ORIGINAL_UPDATE: Callable[[Any, Any], dict[str, Any] | None] | None = None
_ORIGINAL_LOAD: Callable[..., Any] | None = None


def _gps_dict(pair: tuple[float, float]) -> dict[str, float]:
    return {"latitude": pair[0], "longitude": pair[1]}


def _fit_static_ties(
    ties: list[tuple[tuple[float, float], tuple[float, float]]]
) -> tuple[dict[str, Any], list[float], float] | None:
    """Fit a fixed-scale rigid transform from vendor map metadata only."""
    if len(ties) < 3:
        return None
    mean_x = sum(local[0] for local, _ in ties) / len(ties)
    mean_y = sum(local[1] for local, _ in ties) / len(ties)
    mean_lat = sum(gps[0] for _, gps in ties) / len(ties)
    mean_lon = sum(gps[1] for _, gps in ties) / len(ties)

    dot_sum = 0.0
    cross_sum = 0.0
    local_energy = 0.0
    offsets: list[tuple[float, float]] = []
    for (x, y), (lat, lon) in ties:
        offset = _georeference.wgs84_offset_m(mean_lat, mean_lon, lat, lon)
        if offset is None:
            return None
        east, north = offset
        offsets.append((east, north))
        local_x = x - mean_x
        local_y = y - mean_y
        dot_sum += local_x * east + local_y * north
        cross_sum += local_x * north - local_y * east
        local_energy += local_x * local_x + local_y * local_y
    if local_energy <= 1e-9:
        return None

    rotation = -math.atan2(cross_sum, dot_sum)
    observed_scale = math.hypot(dot_sum, cross_sum) / local_energy
    transform = {
        "schema_version": 1,
        "source": _STATIC_SOURCE,
        "reference": {
            "local_x": mean_x,
            "local_y": mean_y,
            "latitude": mean_lat,
            "longitude": mean_lon,
        },
        "rotation_rad": rotation,
    }
    residuals: list[float] = []
    for ((x, y), _), (east, north) in zip(ties, offsets, strict=True):
        dx = x - mean_x
        dy = y - mean_y
        calc_east = dx * math.cos(rotation) + dy * math.sin(rotation)
        calc_north = -dx * math.sin(rotation) + dy * math.cos(rotation)
        residuals.append(math.hypot(calc_east - east, calc_north - north))
    return transform, residuals, observed_scale


def _rtk_anchor(geom: dict[str, Any]) -> tuple[float, float, float | None] | None:
    """Parse ``RTK_anchor: lat lon altitude`` from X3 map-detail metadata."""
    rtk = geom.get("rtk")
    if not isinstance(rtk, dict):
        return None
    raw = rtk.get("anchor")
    if not isinstance(raw, str):
        return None
    mode = re.search(r"RTK_mode:\s*(\d+)", raw)
    match = re.search(
        r"RTK_anchor:\s*([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)(?:\s+([-+]?\d+(?:\.\d+)?))?",
        raw,
    )
    if match is None or (mode is not None and mode.group(1) != "1"):
        return None
    latitude = _georeference._float(match.group(1))  # noqa: SLF001
    longitude = _georeference._float(match.group(2))  # noqa: SLF001
    altitude = _georeference._float(match.group(3)) if match.group(3) else None  # noqa: SLF001
    if (
        latitude is None
        or longitude is None
        or not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0)
        or (abs(latitude) < 1e-12 and abs(longitude) < 1e-12)
    ):
        return None
    return latitude, longitude, altitude


def _rtk_pile(geom: dict[str, Any]) -> list[float] | None:
    """Parse the X3 ``LRTK`` pile record for diagnostics/frame evidence."""
    rtk = geom.get("rtk")
    raw = rtk.get("pile") if isinstance(rtk, dict) else None
    if not isinstance(raw, str):
        return None
    match = re.match(
        r"\s*LRTK\s+([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)",
        raw,
    )
    if match is None:
        return None
    return [float(match.group(index)) for index in range(1, 5)]


def _rtk_bias(geom: dict[str, Any]) -> dict[str, Any] | None:
    rtk = geom.get("rtk")
    raw = rtk.get("bias") if isinstance(rtk, dict) else None
    if not isinstance(raw, str):
        return None
    result: dict[str, Any] = {}
    match = re.search(
        r"nrtk_lrtk_bias:\s*([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)",
        raw,
    )
    if match is not None:
        result["nrtk_lrtk_bias"] = [float(match.group(i)) for i in range(1, 4)]
    refined = re.search(r"nrtk_lrtk_bias_refined:\s*(\d+)", raw)
    if refined is not None:
        result["refined"] = refined.group(1) == "1"
    return result or None


def _apply_x3_rtk_anchor(
    geom: dict[str, Any], candidate: dict[str, Any], origin: tuple[float, float]
) -> dict[str, Any]:
    """Use X3 RTK anchor as local (0,0) WGS84 translation when available.

    Static map ties still provide rotation/scale evidence. The RTK anchor only
    replaces their common absolute translation, which is the part observed to be
    offset on X390 maps.
    """
    anchor = _rtk_anchor(geom)
    pile = _rtk_pile(geom)
    if anchor is None or pile is None:
        return candidate

    latitude, longitude, altitude = anchor
    offset = _georeference.wgs84_offset_m(
        origin[0], origin[1], latitude, longitude
    )
    if offset is None:
        return candidate
    east_m, north_m = offset

    result = deepcopy(candidate)
    result["source"] = _X3_RTK_SOURCE
    result["anchor_policy"] = "x3_rtk_anchor_primary"
    result["reference"] = {
        "local_x": 0.0,
        "local_y": 0.0,
        "latitude": latitude,
        "longitude": longitude,
    }
    result["rtk_anchor"] = {
        "latitude": latitude,
        "longitude": longitude,
        "altitude_m": altitude,
        "local_x": 0.0,
        "local_y": 0.0,
    }
    result["rtk_validation"] = {
        "status": "validated",
        "valid": True,
        "translation_source": "RTK_anchor",
        "rotation_source": "vendor_static_tie_fit",
        "map_origin_difference": {
            "east_m": round(east_m, 3),
            "north_m": round(north_m, 3),
            "distance_m": round(math.hypot(east_m, north_m), 3),
            "bearing_deg_from_north": round(
                (math.degrees(math.atan2(east_m, north_m)) + 360.0) % 360.0,
                2,
            ),
        },
        # Vendor LRTK pile uses Y/X ordering in observed X390 payloads; retain
        # both raw values and the interpreted map-plane pair for diagnostics.
        "pile_raw": pile,
        "pile_local_xy_interpreted": [pile[1], pile[0]],
    }
    bias = _rtk_bias(geom)
    if bias is not None:
        result["rtk_validation"]["bias"] = bias
    return result


def _static_map_georeference(geom: Any) -> dict[str, Any] | None:
    """Build a static vendor transform from origin/center/SW/NE map ties."""
    if not isinstance(geom, dict):
        return None
    # Keep H2-style maps on the established explicit-angle path.
    if _georeference._float(geom.get("map_north_offset")) is not None:  # noqa: SLF001
        return None

    origin = _georeference._gps(geom.get("origin_gps"))  # noqa: SLF001
    center_gps = _georeference._gps(geom.get("center_gps"))  # noqa: SLF001
    sw_gps = _georeference._gps(geom.get("sw_gps"))  # noqa: SLF001
    ne_gps = _georeference._gps(geom.get("ne_gps"))  # noqa: SLF001
    center = _georeference._xy(geom.get("map_circle_center"))  # noqa: SLF001
    width = _georeference._float(geom.get("map_width"))  # noqa: SLF001
    height = _georeference._float(geom.get("map_height"))  # noqa: SLF001
    if None in (origin, center_gps, sw_gps, ne_gps, center, width, height):
        return None
    assert origin and center_gps and sw_gps and ne_gps and center
    assert width is not None and height is not None
    if width <= 0.0 or height <= 0.0 or math.hypot(width, height) < _STATIC_MIN_DIAGONAL_M:
        return None

    cx, cy = center
    local_sw = (cx - width / 2.0, cy - height / 2.0)
    local_ne = (cx + width / 2.0, cy + height / 2.0)
    ties = [
        ((0.0, 0.0), origin),
        (center, center_gps),
        (local_sw, sw_gps),
        (local_ne, ne_gps),
    ]
    fitted = _fit_static_ties(ties)
    if fitted is None:
        return None
    candidate, residuals, observed_scale = fitted
    max_error = max(residuals)
    if (
        max_error > _STATIC_MAX_POINT_ERROR_M
        or not (_STATIC_MIN_SCALE <= observed_scale <= _STATIC_MAX_SCALE)
    ):
        return None

    candidate.update(
        {
            "status": "validated",
            "anchor_policy": "static_map_primary",
            "origin": _gps_dict(origin),
            "center": _gps_dict(center_gps),
            "bounds": {
                "south_west": _gps_dict(sw_gps),
                "north_east": _gps_dict(ne_gps),
            },
            "static_validation": {
                "status": "validated",
                "valid": True,
                "tie_count": len(ties),
                "origin_error_m": round(residuals[0], 3),
                "center_error_m": round(residuals[1], 3),
                "south_west_error_m": round(residuals[2], 3),
                "north_east_error_m": round(residuals[3], 3),
                "rms_error_m": round(
                    math.sqrt(sum(value * value for value in residuals) / len(residuals)),
                    3,
                ),
                "max_error_m": round(max_error, 3),
                "limit_m": _STATIC_MAX_POINT_ERROR_M,
                "rotation_deg": round(math.degrees(candidate["rotation_rad"]), 4),
                "observed_scale": round(observed_scale, 6),
                "scale_limits": [_STATIC_MIN_SCALE, _STATIC_MAX_SCALE],
            },
        }
    )
    return _apply_x3_rtk_anchor(geom, candidate, origin)


def _from_geometry(geom: Any) -> dict[str, Any] | None:
    if _ORIGINAL_FROM_GEOMETRY is None:
        return None
    explicit = _ORIGINAL_FROM_GEOMETRY(geom)
    return explicit if explicit is not None else _static_map_georeference(geom)


def _learned_fit(map_geometry: dict[str, Any], result: Any) -> dict[str, Any] | None:
    calibration = map_geometry.get("_georeference_calibration")
    if isinstance(calibration, dict):
        fit = calibration.get("fit")
        if isinstance(fit, dict) and _georeference.georeference_is_valid(fit):
            return fit
    if isinstance(result, dict) and result.get("source") == "cloud_location_fit" and _georeference.georeference_is_valid(result):
        return result
    return None


def _static_primary_active(map_geometry: dict[str, Any], location: Any, result: Any) -> dict[str, Any] | None:
    vendor = map_geometry.get("_vendor_georeference")
    if (
        not isinstance(vendor, dict)
        or vendor.get("source") not in {_STATIC_SOURCE, _X3_RTK_SOURCE}
        or (vendor.get("static_validation") or {}).get("valid") is not True
    ):
        return None

    calibration = map_geometry.get("_georeference_calibration")
    calibration = calibration if isinstance(calibration, dict) else {}
    learned = _learned_fit(map_geometry, result)
    active = deepcopy(vendor)
    active.update(
        {
            "schema_version": 2,
            "source": vendor.get("source"),
            "status": "validated",
            "map_revision": str(map_geometry.get("revision") or ""),
            "anchor_policy": vendor.get("anchor_policy") or "static_map_primary",
            "calibration": _georeference._calibration_summary(calibration),  # noqa: SLF001
        }
    )
    if isinstance(result, dict):
        for key in ("refinement_stage", "refinement_policy"):
            if result.get(key) is not None:
                active[key] = result[key]

    cloud_checked = _georeference.validate_georeference(
        vendor,
        location,
        limit_m=_georeference._LEARNED_VALIDATION_LIMIT_M,  # noqa: SLF001
    )
    active["cloud_validation"] = dict((cloud_checked or {}).get("validation") or {})

    learned_hint: dict[str, Any] = {"available": learned is not None}
    if learned is not None:
        _, hint = _georeference._vendor_hint(vendor, location, learned)  # noqa: SLF001
        if isinstance(hint, dict):
            learned_hint["position_difference_m"] = hint.get("learned_position_difference_m")
            learned_hint["rotation_difference_deg"] = hint.get("learned_rotation_difference_deg")
    active["learned_hint"] = learned_hint
    return active


def _update(map_geometry: Any, location: Any) -> dict[str, Any] | None:
    if _ORIGINAL_UPDATE is None:
        return None
    result = _ORIGINAL_UPDATE(map_geometry, location)
    if not isinstance(map_geometry, dict):
        return result
    map_geometry[_PROBE_MARKER] = True
    static_active = _static_primary_active(map_geometry, location, result)
    if static_active is not None:
        map_geometry["georeference"] = static_active
        return static_active
    return result


async def _load_persistent_state(self: Any) -> None:
    """Force one map-detail refresh for caches that predate X3 RTK anchoring."""
    if _ORIGINAL_LOAD is None:
        return
    await _ORIGINAL_LOAD(self)
    geometry = getattr(self, "_map_geometry", None)
    if not isinstance(geometry, dict) or geometry.get(_PROBE_MARKER):
        return
    georeference = geometry.get("georeference")
    source = georeference.get("source") if isinstance(georeference, dict) else None
    if source in {"cloud_location_fit", _STATIC_SOURCE}:
        # Older persisted geometry does not retain the raw RTK strings. Re-fetch
        # map-detail once so X3 can discover RTK_anchor while i1 simply rebuilds
        # the same validated static anchor.
        self._map_cache_key = None  # noqa: SLF001


def install_georeference_static_anchor_semantics() -> None:
    """Install static/RTK-anchor detection after adaptive learned-fit policy."""
    global _INSTALLED, _ORIGINAL_FROM_GEOMETRY, _ORIGINAL_UPDATE, _ORIGINAL_LOAD
    if _INSTALLED:
        return
    from . import coordinator_semantics as _coordinator_semantics

    _ORIGINAL_FROM_GEOMETRY = _georeference.georeference_from_geometry
    _ORIGINAL_UPDATE = _georeference.update_georeference
    _ORIGINAL_LOAD = _coordinator_semantics.NavimowCoordinator.async_load_persistent_state
    _georeference.georeference_from_geometry = _from_geometry
    _georeference.update_georeference = _update
    _coordinator_semantics.update_georeference = _update
    _coordinator_semantics.NavimowCoordinator.async_load_persistent_state = _load_persistent_state
    _INSTALLED = True


__all__ = ["install_georeference_static_anchor_semantics"]
