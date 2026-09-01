"""Use WGS84 ellipsoid curvature for Navimower local geodesy.

The core georeference model works on small local mower/map extents, but its
original metre <-> latitude/longitude helpers used a single spherical Earth
radius. That creates a latitude-dependent scale error which is small in Estonia
but can reach several decimetres over a 100 m lawn at lower latitudes.

Keep the existing local rigid-transform model and replace only the geodetic
metre conversion with WGS84 meridional and prime-vertical radii of curvature.
This deliberately does not change map anchors, model-family semantics, X3 RTK
bias handling, or the experimental cartographic-frame correction.
"""
from __future__ import annotations

import math
from typing import Any, Callable

from . import georeference as _georeference

_WGS84_A_M = 6378137.0
_WGS84_INV_F = 298.257223563
_WGS84_F = 1.0 / _WGS84_INV_F
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)

_INSTALLED = False
_ORIGINAL_OFFSET_WGS84: Callable[..., tuple[float, float] | None] | None = None
_ORIGINAL_WGS84_OFFSET_M: Callable[..., tuple[float, float] | None] | None = None
_ORIGINAL_DISTANCE_M: Callable[..., float] | None = None


def _curvature_radii(latitude_rad: float) -> tuple[float, float]:
    """Return WGS84 meridional (M) and prime-vertical (N) radii in metres."""
    sin_lat = math.sin(latitude_rad)
    denominator = 1.0 - _WGS84_E2 * sin_lat * sin_lat
    root = math.sqrt(denominator)
    prime_vertical = _WGS84_A_M / root
    meridional = _WGS84_A_M * (1.0 - _WGS84_E2) / (denominator * root)
    return meridional, prime_vertical


def _shortest_longitude_delta_rad(longitude_a: float, longitude_b: float) -> float:
    """Return the signed shortest longitude delta from A to B in radians."""
    delta_deg = (longitude_b - longitude_a + 180.0) % 360.0 - 180.0
    return math.radians(delta_deg)


def offset_wgs84_ellipsoid(
    latitude: Any,
    longitude: Any,
    east_m: Any,
    north_m: Any,
) -> tuple[float, float] | None:
    """Offset one WGS84 point by local East/North metres on the WGS84 ellipsoid."""
    lat = _georeference._float(latitude)  # noqa: SLF001
    lon = _georeference._float(longitude)  # noqa: SLF001
    east = _georeference._float(east_m)  # noqa: SLF001
    north = _georeference._float(north_m)  # noqa: SLF001
    if None in (lat, lon, east, north):
        return None
    assert lat is not None and lon is not None and east is not None and north is not None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None

    lat0 = math.radians(lat)
    target_lat = lat0

    # Solve dphi = north / M(mean latitude). Two iterations are already more
    # than sufficient for mower-sized extents; use three for symmetric roundtrip
    # behaviour without pulling in a geodesic dependency.
    for _ in range(3):
        mean_lat = (lat0 + target_lat) / 2.0
        meridional, _ = _curvature_radii(mean_lat)
        target_lat = lat0 + north / meridional

    if not (-math.pi / 2.0 <= target_lat <= math.pi / 2.0):
        return None

    mean_lat = (lat0 + target_lat) / 2.0
    _, prime_vertical = _curvature_radii(mean_lat)
    east_radius = prime_vertical * math.cos(mean_lat)
    if abs(east_radius) < 1e-9:
        return None

    target_lon = math.radians(lon) + east / east_radius
    target_lon_deg = (math.degrees(target_lon) + 180.0) % 360.0 - 180.0
    return math.degrees(target_lat), target_lon_deg


def wgs84_offset_m_ellipsoid(
    origin_latitude: Any,
    origin_longitude: Any,
    latitude: Any,
    longitude: Any,
) -> tuple[float, float] | None:
    """Return local East/North metres between nearby WGS84 points."""
    lat0 = _georeference._float(origin_latitude)  # noqa: SLF001
    lon0 = _georeference._float(origin_longitude)  # noqa: SLF001
    lat = _georeference._float(latitude)  # noqa: SLF001
    lon = _georeference._float(longitude)  # noqa: SLF001
    if None in (lat0, lon0, lat, lon):
        return None
    assert lat0 is not None and lon0 is not None and lat is not None and lon is not None
    if not (
        -90.0 <= lat0 <= 90.0
        and -90.0 <= lat <= 90.0
        and -180.0 <= lon0 <= 180.0
        and -180.0 <= lon <= 180.0
    ):
        return None

    lat0_rad = math.radians(lat0)
    lat_rad = math.radians(lat)
    mean_lat = (lat0_rad + lat_rad) / 2.0
    meridional, prime_vertical = _curvature_radii(mean_lat)
    dlat = lat_rad - lat0_rad
    dlon = _shortest_longitude_delta_rad(lon0, lon)
    east = dlon * prime_vertical * math.cos(mean_lat)
    north = dlat * meridional
    return east, north


def distance_m_ellipsoid(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Return local ellipsoidal distance for georeference validation extents."""
    offset = wgs84_offset_m_ellipsoid(
        latitude_a,
        longitude_a,
        latitude_b,
        longitude_b,
    )
    if offset is None:
        return math.inf
    return math.hypot(offset[0], offset[1])


def install_georeference_geodesy_semantics() -> None:
    """Replace spherical local geodesy helpers with WGS84 ellipsoid helpers."""
    global _INSTALLED, _ORIGINAL_OFFSET_WGS84, _ORIGINAL_WGS84_OFFSET_M, _ORIGINAL_DISTANCE_M
    if _INSTALLED:
        return

    _ORIGINAL_OFFSET_WGS84 = _georeference.offset_wgs84
    _ORIGINAL_WGS84_OFFSET_M = _georeference.wgs84_offset_m
    _ORIGINAL_DISTANCE_M = _georeference._distance_m  # noqa: SLF001

    _georeference.offset_wgs84 = offset_wgs84_ellipsoid
    _georeference.wgs84_offset_m = wgs84_offset_m_ellipsoid
    _georeference._distance_m = distance_m_ellipsoid  # noqa: SLF001
    _INSTALLED = True


__all__ = [
    "install_georeference_geodesy_semantics",
    "offset_wgs84_ellipsoid",
    "wgs84_offset_m_ellipsoid",
]
