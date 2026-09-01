"""Use WGS84 ellipsoid curvature for Navimower local geodesy.

The core georeference model works on small local mower/map extents, but its
original metre <-> latitude/longitude helpers used a single spherical Earth
radius. That creates a latitude-dependent scale error which is small in Estonia
but can reach several decimetres over a 100 m lawn at lower latitudes.

Keep the existing local rigid-transform model and replace only the geodetic
metre conversion with WGS84 meridional and prime-vertical radii of curvature.
The state wrapper also invalidates one persisted spherical georeference so fresh
raw exports cannot silently reuse fits learned with the retired metre model.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Callable

from . import georeference as _georeference

_WGS84_A_M = 6378137.0
_WGS84_INV_F = 298.257223563
_WGS84_F = 1.0 / _WGS84_INV_F
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)
_GEODESY_MODEL = "wgs84_ellipsoid_v1"

_HELPERS_INSTALLED = False
_STATE_INSTALLED = False
_ORIGINAL_OFFSET_WGS84: Callable[..., tuple[float, float] | None] | None = None
_ORIGINAL_WGS84_OFFSET_M: Callable[..., tuple[float, float] | None] | None = None
_ORIGINAL_DISTANCE_M: Callable[..., float] | None = None
_ORIGINAL_UPDATE: Callable[[Any, Any], dict[str, Any] | None] | None = None
_ORIGINAL_LOAD: Callable[..., Any] | None = None


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

    # Solve dphi = north / M(mean latitude). Three iterations are inexpensive
    # and make the small-offset inverse symmetric enough for mower-sized maps.
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


def _legacy_geometry(geometry: Any) -> bool:
    if not isinstance(geometry, dict):
        return False
    active = geometry.get("georeference")
    if isinstance(active, dict) and active.get("geodesy_model") == _GEODESY_MODEL:
        return False
    return bool(
        isinstance(active, dict)
        or isinstance(geometry.get("_georeference_calibration"), dict)
        or isinstance(geometry.get("_vendor_georeference"), dict)
    )


def _drop_legacy_georeference(geometry: dict[str, Any]) -> None:
    """Remove only derived absolute georeference state; preserve local map data."""
    geometry.pop("georeference", None)
    geometry.pop("_georeference_calibration", None)
    geometry.pop("_vendor_georeference", None)
    # Old probe markers describe the discarded derived reference state. They may
    # otherwise suppress a one-time refresh in the wrapped semantic layers.
    for key in list(geometry):
        if key.startswith("x3_rtk_anchor_v") or key.startswith("x3_rtk_bias_v"):
            geometry.pop(key, None)
        elif key.startswith("etrs89_cartographic_v"):
            geometry.pop(key, None)


def _update_with_geodesy_model(map_geometry: Any, location: Any) -> dict[str, Any] | None:
    if _ORIGINAL_UPDATE is None:
        return None
    if isinstance(map_geometry, dict):
        calibration = map_geometry.get("_georeference_calibration")
        if isinstance(calibration, dict) and calibration.get("geodesy_model") != _GEODESY_MODEL:
            # A stale cloud fit must not be frozen/refined using a different
            # metre model. The startup wrapper normally handles this earlier;
            # this guard also covers unusual tests/reloads.
            map_geometry.pop("_georeference_calibration", None)
            active = map_geometry.get("georeference")
            if isinstance(active, dict) and active.get("source") == "cloud_location_fit":
                map_geometry.pop("georeference", None)

    result = _ORIGINAL_UPDATE(map_geometry, location)
    if not isinstance(result, dict):
        return result

    decorated = deepcopy(result)
    decorated["geodesy_model"] = _GEODESY_MODEL
    if isinstance(map_geometry, dict):
        map_geometry["georeference"] = decorated
        calibration = map_geometry.get("_georeference_calibration")
        if isinstance(calibration, dict):
            calibration["geodesy_model"] = _GEODESY_MODEL
    return decorated


async def _load_persistent_state(self: Any) -> None:
    """Invalidate one cached spherical reference and force fresh map decoding."""
    if _ORIGINAL_LOAD is None:
        return
    await _ORIGINAL_LOAD(self)
    geometry = getattr(self, "_map_geometry", None)
    if not _legacy_geometry(geometry):
        return
    assert isinstance(geometry, dict)
    _drop_legacy_georeference(geometry)
    # Keep cached local polygons visible, but force a fresh vendor map-detail
    # decode so static ties/RTK metadata are rebuilt with WGS84 ellipsoid metres.
    self._map_cache_key = None  # noqa: SLF001


def install_georeference_geodesy_semantics() -> None:
    """Replace spherical local geodesy helpers with WGS84 ellipsoid helpers."""
    global _HELPERS_INSTALLED, _ORIGINAL_OFFSET_WGS84, _ORIGINAL_WGS84_OFFSET_M, _ORIGINAL_DISTANCE_M
    if _HELPERS_INSTALLED:
        return

    _ORIGINAL_OFFSET_WGS84 = _georeference.offset_wgs84
    _ORIGINAL_WGS84_OFFSET_M = _georeference.wgs84_offset_m
    _ORIGINAL_DISTANCE_M = _georeference._distance_m  # noqa: SLF001

    _georeference.offset_wgs84 = offset_wgs84_ellipsoid
    _georeference.wgs84_offset_m = wgs84_offset_m_ellipsoid
    _georeference._distance_m = distance_m_ellipsoid  # noqa: SLF001
    _HELPERS_INSTALLED = True


def install_georeference_geodesy_state_semantics() -> None:
    """Wrap the final georeference pipeline with model marking/migration."""
    global _STATE_INSTALLED, _ORIGINAL_UPDATE, _ORIGINAL_LOAD
    if _STATE_INSTALLED:
        return

    from . import coordinator_semantics as _coordinator_semantics

    _ORIGINAL_UPDATE = _georeference.update_georeference
    _ORIGINAL_LOAD = _coordinator_semantics.NavimowCoordinator.async_load_persistent_state
    _georeference.update_georeference = _update_with_geodesy_model
    _coordinator_semantics.update_georeference = _update_with_geodesy_model
    _coordinator_semantics.NavimowCoordinator.async_load_persistent_state = _load_persistent_state
    _STATE_INSTALLED = True


__all__ = [
    "install_georeference_geodesy_semantics",
    "install_georeference_geodesy_state_semantics",
    "offset_wgs84_ellipsoid",
    "wgs84_offset_m_ellipsoid",
]
