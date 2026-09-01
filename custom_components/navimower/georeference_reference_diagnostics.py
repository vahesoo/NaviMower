"""Read-only absolute-reference diagnostics for map georeferencing."""
from __future__ import annotations

import math
import re
from typing import Any

from . import georeference as geo

_FLOAT_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def _vector(
    source: tuple[float, float] | None,
    target: tuple[float, float] | None,
) -> dict[str, float] | None:
    """Return target-minus-source East/North metres and bearing."""
    if source is None or target is None:
        return None
    offset = geo.wgs84_offset_m(source[0], source[1], target[0], target[1])
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


def _active_point(active: Any, local: tuple[float, float] | None) -> tuple[float, float] | None:
    if not isinstance(active, dict) or local is None:
        return None
    return geo.local_xy_to_wgs84(active, local[0], local[1])


def _rtk_anchor(geometry: dict[str, Any]) -> tuple[float, float] | None:
    rtk = geometry.get("rtk")
    raw = rtk.get("anchor") if isinstance(rtk, dict) else None
    if not isinstance(raw, str):
        return None
    match = re.search(rf"RTK_anchor:\s*({_FLOAT_RE})\s+({_FLOAT_RE})", raw)
    if match is None:
        return None
    latitude = geo._float(match.group(1))  # noqa: SLF001
    longitude = geo._float(match.group(2))  # noqa: SLF001
    if latitude is None or longitude is None:
        return None
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return None
    return latitude, longitude


def _learned_fit(geometry: dict[str, Any]) -> dict[str, Any] | None:
    calibration = geometry.get("_georeference_calibration")
    if not isinstance(calibration, dict):
        return None
    fit = calibration.get("fit")
    return fit if isinstance(fit, dict) and geo.georeference_is_valid(fit) else None


def _add_candidate(
    candidates: dict[str, Any],
    name: str,
    active_point: tuple[float, float] | None,
    candidate_point: tuple[float, float] | None,
    *,
    meaning: str,
) -> None:
    delta = _vector(active_point, candidate_point)
    if delta is not None:
        candidates[name] = {
            "meaning": meaning,
            "candidate_minus_active": delta,
        }


def reference_candidate_diagnostics(
    geometry: Any,
    active: Any,
    location: Any = None,
    *,
    docked: bool = False,
) -> dict[str, Any]:
    """Compare all cached absolute map references without changing the map.

    All exported values are relative metre vectors. Raw WGS84 coordinates are
    deliberately omitted so Home Assistant Download diagnostics stays safe to
    share while still exposing absolute-frame disagreements.
    """
    if not isinstance(geometry, dict) or not isinstance(active, dict):
        return {"read_only": True, "available": False}

    center_local = geo._xy(geometry.get("map_circle_center"))  # noqa: SLF001
    origin_local = (0.0, 0.0)
    width = geo._float(geometry.get("map_width"))  # noqa: SLF001
    height = geo._float(geometry.get("map_height"))  # noqa: SLF001
    sw_local = ne_local = None
    if center_local is not None and width is not None and height is not None:
        sw_local = (center_local[0] - width / 2.0, center_local[1] - height / 2.0)
        ne_local = (center_local[0] + width / 2.0, center_local[1] + height / 2.0)

    candidates: dict[str, Any] = {}
    _add_candidate(
        candidates,
        "vendor_origin_gps",
        _active_point(active, origin_local),
        geo._gps(geometry.get("origin_gps")),  # noqa: SLF001
        meaning="vendor origin_gps at local map (0,0)",
    )
    _add_candidate(
        candidates,
        "vendor_center_gps",
        _active_point(active, center_local),
        geo._gps(geometry.get("center_gps")),  # noqa: SLF001
        meaning="vendor center_gps at map_circle_center",
    )
    _add_candidate(
        candidates,
        "vendor_south_west_gps",
        _active_point(active, sw_local),
        geo._gps(geometry.get("sw_gps")),  # noqa: SLF001
        meaning="vendor sw_gps at derived local south-west corner",
    )
    _add_candidate(
        candidates,
        "vendor_north_east_gps",
        _active_point(active, ne_local),
        geo._gps(geometry.get("ne_gps")),  # noqa: SLF001
        meaning="vendor ne_gps at derived local north-east corner",
    )
    _add_candidate(
        candidates,
        "rtk_anchor",
        _active_point(active, origin_local),
        _rtk_anchor(geometry),
        meaning="RTK_anchor interpreted as local map (0,0)",
    )

    explicit = geo.georeference_from_geometry(geometry)
    if explicit is not None:
        _add_candidate(
            candidates,
            "explicit_vendor_transform_at_origin",
            _active_point(active, origin_local),
            _active_point(explicit, origin_local),
            meaning="explicit map_north_offset vendor transform evaluated at local (0,0)",
        )

    learned = _learned_fit(geometry)
    if learned is not None:
        _add_candidate(
            candidates,
            "learned_cloud_fit_at_origin",
            _active_point(active, origin_local),
            _active_point(learned, origin_local),
            meaning="persisted cloud learned fit evaluated at local (0,0)",
        )

    gps = None
    if isinstance(location, dict):
        x = geo._float(location.get("posture_x"))  # noqa: SLF001
        y = geo._float(location.get("posture_y"))  # noqa: SLF001
        latitude = geo._float(location.get("latitude"))  # noqa: SLF001
        longitude = geo._float(location.get("longitude"))  # noqa: SLF001
        local = (x, y) if x is not None and y is not None else None
        if latitude is not None and longitude is not None:
            if -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0:
                gps = (latitude, longitude)
        _add_candidate(
            candidates,
            "private_cloud_live_gps",
            _active_point(active, local),
            gps,
            meaning="current private-cloud GPS at current private-cloud local X/Y",
        )

        station = geometry.get("station") if isinstance(geometry.get("station"), dict) else {}
        station_x = geo._float(station.get("x"))  # noqa: SLF001
        station_y = geo._float(station.get("y"))  # noqa: SLF001
        if docked and station_x is not None and station_y is not None:
            _add_candidate(
                candidates,
                "docked_private_cloud_station_anchor",
                _active_point(active, (station_x, station_y)),
                gps,
                meaning="live docked GPS treated as the map station local point",
            )

    rtk = geometry.get("rtk") if isinstance(geometry.get("rtk"), dict) else {}
    raw_bias = rtk.get("bias") if isinstance(rtk, dict) else None
    raw_pile = rtk.get("pile") if isinstance(rtk, dict) else None
    metadata = {
        "map_north_offset_present": geo._float(geometry.get("map_north_offset")) is not None,  # noqa: SLF001
        "origin_gps_present": geo._gps(geometry.get("origin_gps")) is not None,  # noqa: SLF001
        "center_gps_present": geo._gps(geometry.get("center_gps")) is not None,  # noqa: SLF001
        "south_west_gps_present": geo._gps(geometry.get("sw_gps")) is not None,  # noqa: SLF001
        "north_east_gps_present": geo._gps(geometry.get("ne_gps")) is not None,  # noqa: SLF001
        "rtk_anchor_present": _rtk_anchor(geometry) is not None,
        "rtk_bias_present": isinstance(raw_bias, str) and "nrtk_lrtk_bias" in raw_bias,
        "rtk_pile_present": isinstance(raw_pile, str) and "LRTK" in raw_pile,
        "learned_fit_present": learned is not None,
        "station_present": isinstance(geometry.get("station"), dict),
    }

    rotation = geo._float(active.get("rotation_rad"))  # noqa: SLF001
    return {
        "read_only": True,
        "available": True,
        "convention": "candidate_minus_active; east/north metres; bearing clockwise from north",
        "active_source": active.get("source"),
        "active_anchor_policy": active.get("anchor_policy"),
        "active_rotation_deg": round(math.degrees(rotation), 4) if rotation is not None else None,
        "vendor_metadata": metadata,
        "candidate_offsets": candidates,
    }


__all__ = ["reference_candidate_diagnostics"]
