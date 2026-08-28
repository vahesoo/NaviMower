"""Map georeference helpers for Navimower.

The mower map uses its own local X/Y frame. Vendor map metadata ties that frame
to WGS84 through one local reference point plus ``map_north_offset``.  Keep the
vendor details behind a small Navimower-owned schema so frontends do not need to
understand private-cloud field names.
"""
from __future__ import annotations

import base64
import io
import json
import math
from typing import Any

_EARTH_RADIUS_M = 6378137.0
_GEOREFERENCE_SCHEMA_VERSION = 1
_DEFAULT_VALIDATION_LIMIT_M = 2.0


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _xy(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    x, y = _float(value[0]), _float(value[1])
    return (x, y) if x is not None and y is not None else None


def _gps(value: Any) -> tuple[float, float] | None:
    """Return vendor [longitude, latitude] as (latitude, longitude)."""
    pair = _xy(value)
    if pair is None:
        return None
    longitude, latitude = pair
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return None
    return latitude, longitude


def _gps_dict(value: Any) -> dict[str, float] | None:
    pair = _gps(value)
    if pair is None:
        return None
    latitude, longitude = pair
    return {"latitude": latitude, "longitude": longitude}


def georeference_from_geometry(geom: Any) -> dict[str, Any] | None:
    """Normalize raw vendor map georeference fields."""
    if not isinstance(geom, dict):
        return None
    local = _xy(geom.get("map_circle_center"))
    center = _gps(geom.get("center_gps"))
    rotation = _float(geom.get("map_north_offset"))
    if local is None or center is None or rotation is None:
        return None

    local_x, local_y = local
    latitude, longitude = center
    result: dict[str, Any] = {
        "schema_version": _GEOREFERENCE_SCHEMA_VERSION,
        "source": "vendor_map_detail",
        "reference": {
            "local_x": local_x,
            "local_y": local_y,
            "latitude": latitude,
            "longitude": longitude,
        },
        # Positive vendor angle rotates local coordinates clockwise relative to
        # true East/North; local -> EN therefore uses R(-rotation_rad).
        "rotation_rad": rotation,
    }

    origin = _gps_dict(geom.get("origin_gps"))
    north_east = _gps_dict(geom.get("ne_gps"))
    south_west = _gps_dict(geom.get("sw_gps"))
    if origin is not None:
        result["origin"] = origin
    if north_east is not None or south_west is not None:
        result["bounds"] = {
            "north_east": north_east,
            "south_west": south_west,
        }
    return result


def georeference_from_plain_map_detail(data: Any) -> dict[str, Any] | None:
    """Extract normalized georeference from the plain map-detail response."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            return None
    if not isinstance(data, dict):
        return None
    detail = data.get("map_detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except (TypeError, ValueError):
            return None
    return georeference_from_geometry(detail)


def georeference_from_compressed_map_detail(blob: Any) -> dict[str, Any] | None:
    """Extract normalized georeference from the compressed map-detail response."""
    if not isinstance(blob, str) or not blob:
        return None
    try:
        raw = base64.b64decode(blob)
    except Exception:  # noqa: BLE001
        return None

    decompressed: bytes | None = None
    try:
        import compression.zstd as zstd  # type: ignore[import-not-found]

        try:
            decompressed = zstd.decompress(raw)
        except Exception:  # noqa: BLE001
            decompressed = zstd.ZstdDecompressor().decompress(raw)
    except Exception:  # noqa: BLE001
        try:
            import zstandard  # type: ignore[import-not-found]

            decompressed = zstandard.ZstdDecompressor().stream_reader(
                io.BytesIO(raw)
            ).read()
        except Exception:  # noqa: BLE001
            return None
    if not decompressed:
        return None
    try:
        outer = json.loads(decompressed)
        detail = outer.get("map_detail") if isinstance(outer, dict) else None
        if isinstance(detail, str):
            detail = json.loads(detail)
    except (TypeError, ValueError):
        return None
    return georeference_from_geometry(detail)


def local_xy_to_wgs84(
    georeference: dict[str, Any], x: Any, y: Any
) -> tuple[float, float] | None:
    """Transform one local mower X/Y point to WGS84 latitude/longitude."""
    reference = georeference.get("reference") or {}
    local_x = _float(reference.get("local_x"))
    local_y = _float(reference.get("local_y"))
    latitude = _float(reference.get("latitude"))
    longitude = _float(reference.get("longitude"))
    rotation = _float(georeference.get("rotation_rad"))
    point_x, point_y = _float(x), _float(y)
    if None in (
        local_x,
        local_y,
        latitude,
        longitude,
        rotation,
        point_x,
        point_y,
    ):
        return None

    dx = point_x - local_x
    dy = point_y - local_y
    # Vendor map_north_offset is clockwise in the local map frame.
    east = dx * math.cos(rotation) + dy * math.sin(rotation)
    north = -dx * math.sin(rotation) + dy * math.cos(rotation)
    latitude_rad = math.radians(latitude)
    calculated_latitude = latitude + math.degrees(north / _EARTH_RADIUS_M)
    cos_latitude = math.cos(latitude_rad)
    if abs(cos_latitude) < 1e-12:
        return None
    calculated_longitude = longitude + math.degrees(
        east / (_EARTH_RADIUS_M * cos_latitude)
    )
    return calculated_latitude, calculated_longitude


def _distance_m(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    lat1, lat2 = math.radians(latitude_a), math.radians(latitude_b)
    dlat = lat2 - lat1
    dlon = math.radians(longitude_b - longitude_a)
    hav = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(hav)))


def validate_georeference(
    georeference: Any,
    location: Any,
    *,
    limit_m: float = _DEFAULT_VALIDATION_LIMIT_M,
) -> dict[str, Any] | None:
    """Attach a private-cloud XY/GPS consistency check to one georeference.

    Validation is passive: it uses the existing location response and never
    causes an additional cloud request.
    """
    if not isinstance(georeference, dict):
        return None
    result = dict(georeference)
    if not isinstance(location, dict):
        result["validation"] = {"status": "unavailable", "valid": None}
        return result

    x = _float(location.get("posture_x"))
    y = _float(location.get("posture_y"))
    latitude = _float(location.get("latitude"))
    longitude = _float(location.get("longitude"))
    if None in (x, y, latitude, longitude):
        result["validation"] = {"status": "unavailable", "valid": None}
        return result

    calculated = local_xy_to_wgs84(result, x, y)
    if calculated is None:
        result["validation"] = {"status": "unavailable", "valid": None}
        return result
    calculated_latitude, calculated_longitude = calculated
    error_m = _distance_m(
        calculated_latitude,
        calculated_longitude,
        latitude,
        longitude,
    )
    valid = error_m <= max(0.0, float(limit_m))
    result["validation"] = {
        "status": "validated" if valid else "mismatch",
        "valid": valid,
        "error_m": round(error_m, 3),
        "limit_m": float(limit_m),
        "report_time": location.get("report_time"),
    }
    return result


__all__ = [
    "georeference_from_compressed_map_detail",
    "georeference_from_plain_map_detail",
    "local_xy_to_wgs84",
    "validate_georeference",
]
