"""Prefer vendor static map anchors when map-detail proves a north-aligned frame.

Some Navimow map generations (observed on i1 and X3 families) omit
``map_north_offset`` but still expose a complete static WGS84 frame:

* ``origin_gps`` ties local map coordinate (0, 0) to WGS84;
* ``map_circle_center`` + ``center_gps`` provide an independent center check;
* ``sw_gps`` / ``ne_gps`` plus ``map_width`` / ``map_height`` provide a long
  baseline bounds check.

For those maps the local X/Y axes are already East/North aligned. Accept the
static origin only when all independent vendor fields agree within tight metre
limits. Once accepted, that static map anchor is authoritative for translation
and rotation for the map revision. Cloud-location fitting continues in the
background as diagnostics/validation, but GPS drift may no longer move the map.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Callable

from . import georeference as _georeference

_STATIC_SOURCE = "vendor_map_origin"
_STATIC_MAX_POINT_ERROR_M = 1.0
_STATIC_MIN_DIAGONAL_M = 10.0
_STATIC_MIN_SCALE = 0.97
_STATIC_MAX_SCALE = 1.03

_INSTALLED = False
_ORIGINAL_FROM_GEOMETRY: Callable[[Any], dict[str, Any] | None] | None = None
_ORIGINAL_UPDATE: Callable[[Any, Any], dict[str, Any] | None] | None = None


def _gps_dict(pair: tuple[float, float]) -> dict[str, float]:
    latitude, longitude = pair
    return {"latitude": latitude, "longitude": longitude}


def _point_error_m(
    candidate: dict[str, Any],
    local: tuple[float, float],
    gps: tuple[float, float],
) -> float | None:
    projected = _georeference.local_xy_to_wgs84(candidate, local[0], local[1])
    if projected is None:
        return None
    return _georeference._distance_m(  # noqa: SLF001 - same integration module.
        projected[0], projected[1], gps[0], gps[1]
    )


def _static_map_origin_georeference(geom: Any) -> dict[str, Any] | None:
    """Build a validated local-(0,0) vendor anchor for north-aligned maps."""
    if not isinstance(geom, dict):
        return None

    # A real map_north_offset belongs to the established vendor_map_detail path.
    if _georeference._float(geom.get("map_north_offset")) is not None:  # noqa: SLF001
        return None

    origin = _georeference._gps(geom.get("origin_gps"))  # noqa: SLF001
    center_gps = _georeference._gps(geom.get("center_gps"))  # noqa: SLF001
    south_west_gps = _georeference._gps(geom.get("sw_gps"))  # noqa: SLF001
    north_east_gps = _georeference._gps(geom.get("ne_gps"))  # noqa: SLF001
    center_local = _georeference._xy(geom.get("map_circle_center"))  # noqa: SLF001
    width = _georeference._float(geom.get("map_width"))  # noqa: SLF001
    height = _georeference._float(geom.get("map_height"))  # noqa: SLF001
    if (
        origin is None
        or center_gps is None
        or south_west_gps is None
        or north_east_gps is None
        or center_local is None
        or width is None
        or height is None
        or width <= 0.0
        or height <= 0.0
    ):
        return None

    diagonal_m = math.hypot(width, height)
    if diagonal_m < _STATIC_MIN_DIAGONAL_M:
        return None

    latitude, longitude = origin
    candidate: dict[str, Any] = {
        "schema_version": 1,
        "source": _STATIC_SOURCE,
        "reference": {
            "local_x": 0.0,
            "local_y": 0.0,
            "latitude": latitude,
            "longitude": longitude,
        },
        # Missing map_north_offset is accepted only after the independent
        # center/bounds checks prove that local X/Y is already East/North aligned.
        "rotation_rad": 0.0,
        "origin": _gps_dict(origin),
        "center": _gps_dict(center_gps),
        "bounds": {
            "south_west": _gps_dict(south_west_gps),
            "north_east": _gps_dict(north_east_gps),
        },
    }

    center_x, center_y = center_local
    local_south_west = (center_x - width / 2.0, center_y - height / 2.0)
    local_north_east = (center_x + width / 2.0, center_y + height / 2.0)

    center_error = _point_error_m(candidate, center_local, center_gps)
    south_west_error = _point_error_m(
        candidate, local_south_west, south_west_gps
    )
    north_east_error = _point_error_m(
        candidate, local_north_east, north_east_gps
    )
    if None in (center_error, south_west_error, north_east_error):
        return None

    gps_diagonal_m = _georeference._distance_m(  # noqa: SLF001
        south_west_gps[0],
        south_west_gps[1],
        north_east_gps[0],
        north_east_gps[1],
    )
    observed_scale = gps_diagonal_m / diagonal_m
    max_error = max(center_error, south_west_error, north_east_error)
    valid = (
        max_error <= _STATIC_MAX_POINT_ERROR_M
        and _STATIC_MIN_SCALE <= observed_scale <= _STATIC_MAX_SCALE
    )
    if not valid:
        return None

    candidate["status"] = "validated"
    candidate["anchor_policy"] = "static_map_primary"
    candidate["static_validation"] = {
        "status": "validated",
        "valid": True,
        "rotation_assumption_deg": 0.0,
        "center_error_m": round(center_error, 3),
        "south_west_error_m": round(south_west_error, 3),
        "north_east_error_m": round(north_east_error, 3),
        "max_error_m": round(max_error, 3),
        "limit_m": _STATIC_MAX_POINT_ERROR_M,
        "local_diagonal_m": round(diagonal_m, 3),
        "gps_diagonal_m": round(gps_diagonal_m, 3),
        "observed_scale": round(observed_scale, 6),
        "scale_limits": [_STATIC_MIN_SCALE, _STATIC_MAX_SCALE],
    }
    return candidate


def _from_geometry(geom: Any) -> dict[str, Any] | None:
    if _ORIGINAL_FROM_GEOMETRY is None:
        return None
    existing = _ORIGINAL_FROM_GEOMETRY(geom)
    if existing is not None:
        return existing
    return _static_map_origin_georeference(geom)


def _learned_fit(map_geometry: dict[str, Any], result: Any) -> dict[str, Any] | None:
    calibration = map_geometry.get("_georeference_calibration")
    if isinstance(calibration, dict):
        fit = calibration.get("fit")
        if isinstance(fit, dict) and _georeference.georeference_is_valid(fit):
            return fit
    if (
        isinstance(result, dict)
        and result.get("source") == "cloud_location_fit"
        and _georeference.georeference_is_valid(result)
    ):
        return result
    return None


def _static_primary_active(
    map_geometry: dict[str, Any], location: Any, result: Any
) -> dict[str, Any] | None:
    vendor = map_geometry.get("_vendor_georeference")
    if (
        not isinstance(vendor, dict)
        or vendor.get("source") != _STATIC_SOURCE
        or (vendor.get("static_validation") or {}).get("valid") is not True
    ):
        return None

    revision = str(map_geometry.get("revision") or "")
    calibration = map_geometry.get("_georeference_calibration")
    calibration = calibration if isinstance(calibration, dict) else {}
    learned = _learned_fit(map_geometry, result)

    active = deepcopy(vendor)
    active["schema_version"] = 2
    active["source"] = _STATIC_SOURCE
    active["status"] = "validated"
    active["map_revision"] = revision
    active["anchor_policy"] = "static_map_primary"
    active["calibration"] = _georeference._calibration_summary(calibration)  # noqa: SLF001

    if isinstance(result, dict):
        for key in ("refinement_stage", "refinement_policy"):
            if result.get(key) is not None:
                active[key] = result[key]

    cloud_checked = _georeference.validate_georeference(
        vendor,
        location,
        limit_m=_georeference._LEARNED_VALIDATION_LIMIT_M,  # noqa: SLF001
    )
    active["cloud_validation"] = dict(
        (cloud_checked or {}).get("validation") or {}
    )

    learned_hint: dict[str, Any] = {"available": learned is not None}
    if learned is not None:
        _, hint = _georeference._vendor_hint(  # noqa: SLF001
            vendor, location, learned
        )
        if isinstance(hint, dict):
            if hint.get("learned_position_difference_m") is not None:
                learned_hint["position_difference_m"] = hint[
                    "learned_position_difference_m"
                ]
            if hint.get("learned_rotation_difference_deg") is not None:
                learned_hint["rotation_difference_deg"] = hint[
                    "learned_rotation_difference_deg"
                ]
    active["learned_hint"] = learned_hint
    return active


def _update(map_geometry: Any, location: Any) -> dict[str, Any] | None:
    if _ORIGINAL_UPDATE is None:
        return None
    result = _ORIGINAL_UPDATE(map_geometry, location)
    if not isinstance(map_geometry, dict):
        return result
    static_active = _static_primary_active(map_geometry, location, result)
    if static_active is None:
        return result
    map_geometry["georeference"] = static_active
    return static_active


def install_georeference_static_anchor_semantics() -> None:
    """Install static-origin detection after the adaptive learned-fit policy."""
    global _INSTALLED, _ORIGINAL_FROM_GEOMETRY, _ORIGINAL_UPDATE
    if _INSTALLED:
        return

    from . import coordinator_semantics as _coordinator_semantics

    _ORIGINAL_FROM_GEOMETRY = _georeference.georeference_from_geometry
    _ORIGINAL_UPDATE = _georeference.update_georeference
    _georeference.georeference_from_geometry = _from_geometry
    _georeference.update_georeference = _update
    _coordinator_semantics.update_georeference = _update
    _INSTALLED = True


__all__ = [
    "install_georeference_static_anchor_semantics",
]
