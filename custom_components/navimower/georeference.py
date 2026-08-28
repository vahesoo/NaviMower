"""Map georeference helpers for Navimower.

Navimow maps use a local metric X/Y frame. Some mower/map generations expose
an explicit WGS84 tie point plus ``map_north_offset`` in map-detail; all mower
families observed so far also expose a current local X/Y + WGS84 pair through
the normal private-cloud location response. Keep both sources behind one
Navimower-owned schema so frontends never need to understand vendor fields.
"""
from __future__ import annotations

import base64
import io
import json
import math
from typing import Any

_EARTH_RADIUS_M = 6378137.0
_VENDOR_GEOREFERENCE_SCHEMA_VERSION = 1
_GEOREFERENCE_SCHEMA_VERSION = 2
_DEFAULT_VALIDATION_LIMIT_M = 2.0
_LEARNED_VALIDATION_LIMIT_M = 3.0
_MIN_FIT_SAMPLES = 5
_MIN_FIT_BASELINE_M = 10.0
_MIN_SAMPLE_SEPARATION_M = 1.5
_MAX_FIT_SAMPLES = 24
_MAX_FIT_RMS_ERROR_M = 2.0
_MAX_FIT_ERROR_M = 4.0
_MIN_OBSERVED_SCALE = 0.90
_MAX_OBSERVED_SCALE = 1.10
_RESET_AFTER_MISMATCHES = 3


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
    if abs(latitude) < 1e-12 and abs(longitude) < 1e-12:
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
        "schema_version": _VENDOR_GEOREFERENCE_SCHEMA_VERSION,
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
    east = dx * math.cos(rotation) + dy * math.sin(rotation)
    north = -dx * math.sin(rotation) + dy * math.cos(rotation)
    return offset_wgs84(latitude, longitude, east, north)


def offset_wgs84(
    latitude: Any,
    longitude: Any,
    east_m: Any,
    north_m: Any,
) -> tuple[float, float] | None:
    """Offset one WGS84 point by local East/North metres."""
    lat = _float(latitude)
    lon = _float(longitude)
    east = _float(east_m)
    north = _float(north_m)
    if None in (lat, lon, east, north):
        return None
    latitude_rad = math.radians(lat)
    cos_latitude = math.cos(latitude_rad)
    if abs(cos_latitude) < 1e-12:
        return None
    return (
        lat + math.degrees(north / _EARTH_RADIUS_M),
        lon + math.degrees(east / (_EARTH_RADIUS_M * cos_latitude)),
    )


def wgs84_offset_m(
    origin_latitude: Any,
    origin_longitude: Any,
    latitude: Any,
    longitude: Any,
) -> tuple[float, float] | None:
    """Return approximate East/North metres from one WGS84 point to another."""
    lat0 = _float(origin_latitude)
    lon0 = _float(origin_longitude)
    lat = _float(latitude)
    lon = _float(longitude)
    if None in (lat0, lon0, lat, lon):
        return None
    mean_latitude = math.radians((lat0 + lat) / 2.0)
    east = math.radians(lon - lon0) * _EARTH_RADIUS_M * math.cos(mean_latitude)
    north = math.radians(lat - lat0) * _EARTH_RADIUS_M
    return east, north


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


def georeference_distance_m(first: Any, second: Any) -> float | None:
    """Return distance between two normalized georeference reference points."""
    if not isinstance(first, dict) or not isinstance(second, dict):
        return None
    a = first.get("reference") or {}
    b = second.get("reference") or {}
    lat_a = _float(a.get("latitude"))
    lon_a = _float(a.get("longitude"))
    lat_b = _float(b.get("latitude"))
    lon_b = _float(b.get("longitude"))
    if None in (lat_a, lon_a, lat_b, lon_b):
        return None
    return _distance_m(lat_a, lon_a, lat_b, lon_b)


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
    if (
        None in (x, y, latitude, longitude)
        or not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0)
        or (abs(latitude) < 1e-12 and abs(longitude) < 1e-12)
    ):
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


def _current_location_sample(location: Any) -> dict[str, Any] | None:
    """Return one paired current XY/GPS sample from the normal location poll."""
    if not isinstance(location, dict):
        return None
    x = _float(location.get("posture_x"))
    y = _float(location.get("posture_y"))
    latitude = _float(location.get("latitude"))
    longitude = _float(location.get("longitude"))
    if None in (x, y, latitude, longitude):
        return None
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return None
    if abs(latitude) < 1e-12 and abs(longitude) < 1e-12:
        return None
    return {
        "x": x,
        "y": y,
        "latitude": latitude,
        "longitude": longitude,
        "report_time": location.get("report_time"),
    }


def _sample_distance_m(first: dict[str, Any], second: dict[str, Any]) -> float:
    return math.hypot(
        float(first["x"]) - float(second["x"]),
        float(first["y"]) - float(second["y"]),
    )


def _append_sample(samples: list[dict[str, Any]], sample: dict[str, Any]) -> bool:
    report_time = sample.get("report_time")
    for existing in samples:
        if report_time is not None and existing.get("report_time") == report_time:
            return False
        if _sample_distance_m(existing, sample) < _MIN_SAMPLE_SEPARATION_M:
            return False
    samples.append(sample)
    if len(samples) > _MAX_FIT_SAMPLES:
        del samples[: len(samples) - _MAX_FIT_SAMPLES]
    return True


def _baseline_m(samples: list[dict[str, Any]]) -> float:
    baseline = 0.0
    for index, first in enumerate(samples):
        for second in samples[index + 1 :]:
            baseline = max(baseline, _sample_distance_m(first, second))
    return baseline


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    if not count:
        return 0.0
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _fit_once(
    samples: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[float], float] | None:
    if len(samples) < 2:
        return None
    mean_x = sum(float(item["x"]) for item in samples) / len(samples)
    mean_y = sum(float(item["y"]) for item in samples) / len(samples)
    mean_latitude = sum(float(item["latitude"]) for item in samples) / len(samples)
    mean_longitude = sum(float(item["longitude"]) for item in samples) / len(samples)

    dot_sum = 0.0
    cross_sum = 0.0
    local_energy = 0.0
    for item in samples:
        local_x = float(item["x"]) - mean_x
        local_y = float(item["y"]) - mean_y
        offset = wgs84_offset_m(
            mean_latitude,
            mean_longitude,
            item["latitude"],
            item["longitude"],
        )
        if offset is None:
            return None
        east, north = offset
        dot_sum += local_x * east + local_y * north
        cross_sum += local_x * north - local_y * east
        local_energy += local_x * local_x + local_y * local_y
    if local_energy <= 1e-9:
        return None

    theta = math.atan2(cross_sum, dot_sum)
    rotation_rad = -theta
    observed_scale = math.hypot(dot_sum, cross_sum) / local_energy
    georeference = {
        "schema_version": _GEOREFERENCE_SCHEMA_VERSION,
        "source": "cloud_location_fit",
        "reference": {
            "local_x": mean_x,
            "local_y": mean_y,
            "latitude": mean_latitude,
            "longitude": mean_longitude,
        },
        "rotation_rad": rotation_rad,
    }

    residuals: list[float] = []
    for item in samples:
        calculated = local_xy_to_wgs84(georeference, item["x"], item["y"])
        if calculated is None:
            return None
        residuals.append(
            _distance_m(
                calculated[0],
                calculated[1],
                float(item["latitude"]),
                float(item["longitude"]),
            )
        )
    return georeference, residuals, observed_scale


def _fit_samples(samples: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Fit one robust rigid local-XY -> WGS84 transform with fixed metre scale."""
    if len(samples) < _MIN_FIT_SAMPLES:
        return None
    baseline = _baseline_m(samples)
    if baseline < _MIN_FIT_BASELINE_M:
        return None

    working = list(samples)
    first = _fit_once(working)
    if first is None:
        return None
    _, first_residuals, _ = first
    median = _median(first_residuals)
    mad = _median([abs(value - median) for value in first_residuals])
    reject_limit = max(2.5, median + 3.0 * max(mad, 0.25))
    inliers = [
        item
        for item, residual in zip(working, first_residuals, strict=True)
        if residual <= reject_limit
    ]
    rejected_count = len(working) - len(inliers)
    if len(inliers) >= _MIN_FIT_SAMPLES and len(inliers) < len(working):
        working = inliers

    fitted = _fit_once(working)
    if fitted is None:
        return None
    georeference, residuals, observed_scale = fitted
    rms_error = math.sqrt(sum(value * value for value in residuals) / len(residuals))
    max_error = max(residuals)
    valid = (
        rms_error <= _MAX_FIT_RMS_ERROR_M
        and max_error <= _MAX_FIT_ERROR_M
        and _MIN_OBSERVED_SCALE <= observed_scale <= _MAX_OBSERVED_SCALE
    )
    georeference["status"] = "validated" if valid else "mismatch"
    georeference["calibration"] = {
        "sample_count": len(samples),
        "inlier_count": len(working),
        "rejected_count": rejected_count,
        "baseline_m": round(baseline, 3),
        "rms_error_m": round(rms_error, 3),
        "max_error_m": round(max_error, 3),
        "observed_scale": round(observed_scale, 6),
        "min_samples": _MIN_FIT_SAMPLES,
        "min_baseline_m": _MIN_FIT_BASELINE_M,
    }
    return georeference


def _calibration_summary(calibration: dict[str, Any]) -> dict[str, Any]:
    samples = [
        item for item in calibration.get("samples") or [] if isinstance(item, dict)
    ]
    fit = calibration.get("fit") if isinstance(calibration.get("fit"), dict) else None
    summary = dict((fit or {}).get("calibration") or {})
    summary.setdefault("sample_count", len(samples))
    summary.setdefault(
        "baseline_m", round(_baseline_m(samples), 3) if samples else 0.0
    )
    summary.setdefault("min_samples", _MIN_FIT_SAMPLES)
    summary.setdefault("min_baseline_m", _MIN_FIT_BASELINE_M)
    summary["mismatch_count"] = int(calibration.get("mismatch_count") or 0)
    return summary


def _vendor_hint(
    vendor: dict[str, Any] | None,
    location: Any,
    learned: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(vendor, dict):
        return None, None
    validated = validate_georeference(vendor, location)
    hint: dict[str, Any] = {
        "available": True,
        "validation": dict((validated or {}).get("validation") or {}),
    }
    if isinstance(learned, dict):
        vendor_reference = vendor.get("reference") or {}
        projected = local_xy_to_wgs84(
            learned,
            vendor_reference.get("local_x"),
            vendor_reference.get("local_y"),
        )
        vendor_latitude = _float(vendor_reference.get("latitude"))
        vendor_longitude = _float(vendor_reference.get("longitude"))
        if projected is not None and None not in (vendor_latitude, vendor_longitude):
            hint["learned_position_difference_m"] = round(
                _distance_m(
                    projected[0],
                    projected[1],
                    vendor_latitude,
                    vendor_longitude,
                ),
                3,
            )
        learned_rotation = _float(learned.get("rotation_rad"))
        vendor_rotation = _float(vendor.get("rotation_rad"))
        if learned_rotation is not None and vendor_rotation is not None:
            delta = (
                learned_rotation - vendor_rotation + math.pi
            ) % (2.0 * math.pi) - math.pi
            hint["learned_rotation_difference_deg"] = round(
                abs(math.degrees(delta)), 3
            )
    return validated, hint


def update_georeference(
    map_geometry: Any,
    location: Any,
) -> dict[str, Any] | None:
    """Update and return the integration-owned georeference for one map revision.

    The universal path learns from *current* private-cloud location pairs only.
    ``last_*`` fields are deliberately not used because they have no independent
    revision/timestamp guarantee and may survive a map edit. Once a learned fit
    is validated it is frozen for the map revision; subsequent location samples
    only validate it unless three consecutive mismatches force relearning.
    """
    if not isinstance(map_geometry, dict):
        return None

    revision = str(map_geometry.get("revision") or "")
    calibration = map_geometry.get("_georeference_calibration")
    if not isinstance(calibration, dict) or calibration.get("map_revision") != revision:
        calibration = {
            "map_revision": revision,
            "samples": [],
            "fit": None,
            "mismatch_count": 0,
        }
        map_geometry["_georeference_calibration"] = calibration

    samples = [
        dict(item)
        for item in calibration.get("samples") or []
        if isinstance(item, dict)
    ]
    calibration["samples"] = samples
    sample = _current_location_sample(location)

    fit = calibration.get("fit") if isinstance(calibration.get("fit"), dict) else None
    if fit is None and sample is not None:
        _append_sample(samples, sample)

    if fit is not None:
        validated_fit = validate_georeference(
            fit,
            location,
            limit_m=_LEARNED_VALIDATION_LIMIT_M,
        )
        validation = (validated_fit or {}).get("validation") or {}
        if validation.get("valid") is False:
            calibration["mismatch_count"] = (
                int(calibration.get("mismatch_count") or 0) + 1
            )
        elif validation.get("valid") is True:
            calibration["mismatch_count"] = 0
        calibration["last_validation"] = dict(validation)
        if int(calibration.get("mismatch_count") or 0) >= _RESET_AFTER_MISMATCHES:
            calibration["fit"] = None
            calibration["samples"] = [sample] if sample is not None else []
            calibration["mismatch_count"] = 0
            fit = None
            samples = calibration["samples"]
        else:
            fit = validated_fit or fit

    candidate: dict[str, Any] | None = None
    if fit is None:
        candidate = _fit_samples(samples)
        if candidate is not None and candidate.get("status") == "validated":
            calibration["fit"] = {
                key: value for key, value in candidate.items() if key != "validation"
            }
            calibration["mismatch_count"] = 0
            fit = validate_georeference(
                calibration["fit"],
                location,
                limit_m=_LEARNED_VALIDATION_LIMIT_M,
            ) or calibration["fit"]

    vendor = map_geometry.get("_vendor_georeference")
    if not isinstance(vendor, dict):
        legacy_vendor = map_geometry.get("georeference")
        if (
            isinstance(legacy_vendor, dict)
            and legacy_vendor.get("source") == "vendor_map_detail"
        ):
            vendor = dict(legacy_vendor)
            map_geometry["_vendor_georeference"] = vendor

    vendor_validated, vendor_hint = _vendor_hint(vendor, location, fit)
    summary = _calibration_summary(calibration)

    if isinstance(fit, dict) and (
        fit.get("status") == "validated"
        or (fit.get("validation") or {}).get("valid") is True
    ):
        active = dict(fit)
        active["schema_version"] = _GEOREFERENCE_SCHEMA_VERSION
        active["source"] = "cloud_location_fit"
        active["status"] = "validated"
        active["map_revision"] = revision
        active["calibration"] = summary
        if vendor_hint is not None:
            active["vendor_hint"] = vendor_hint
        map_geometry["georeference"] = active
        return active

    if (
        isinstance(vendor_validated, dict)
        and (vendor_validated.get("validation") or {}).get("valid") is True
    ):
        active = dict(vendor_validated)
        active["schema_version"] = _GEOREFERENCE_SCHEMA_VERSION
        active["source"] = "vendor_map_detail"
        active["status"] = "validated"
        active["map_revision"] = revision
        active["calibration"] = summary
        if vendor_hint is not None:
            active["vendor_hint"] = vendor_hint
        map_geometry["georeference"] = active
        return active

    status = "learning"
    if candidate is not None and candidate.get("status") == "mismatch":
        status = "mismatch"

    active = {
        "schema_version": _GEOREFERENCE_SCHEMA_VERSION,
        "source": "cloud_location_fit",
        "status": status,
        "map_revision": revision,
        "calibration": summary,
    }
    if vendor_hint is not None:
        active["vendor_hint"] = vendor_hint
    map_geometry["georeference"] = active
    return active


def georeference_is_valid(georeference: Any) -> bool:
    """Return whether a georeference has a validated usable transform."""
    if not isinstance(georeference, dict):
        return False
    reference = georeference.get("reference") or {}
    transform_complete = all(
        _float(value) is not None
        for value in (
            reference.get("local_x"),
            reference.get("local_y"),
            reference.get("latitude"),
            reference.get("longitude"),
            georeference.get("rotation_rad"),
        )
    )
    if not transform_complete:
        return False
    if georeference.get("status") == "validated":
        return True
    return (georeference.get("validation") or {}).get("valid") is True


def local_to_site_affine(
    georeference: Any,
    origin_latitude: Any,
    origin_longitude: Any,
) -> dict[str, float] | None:
    """Return an affine local-X/Y -> site East/North transform.

    Consumers can apply this matrix to an entire mower SVG/map group instead of
    converting every point through WGS84 in the browser:

      east  = a * x + c * y + e
      north = b * x + d * y + f
    """
    if not georeference_is_valid(georeference):
        return None
    reference = georeference.get("reference") or {}
    local_x = _float(reference.get("local_x"))
    local_y = _float(reference.get("local_y"))
    latitude = _float(reference.get("latitude"))
    longitude = _float(reference.get("longitude"))
    rotation = _float(georeference.get("rotation_rad"))
    if None in (local_x, local_y, latitude, longitude, rotation):
        return None
    offset = wgs84_offset_m(origin_latitude, origin_longitude, latitude, longitude)
    if offset is None:
        return None
    reference_east, reference_north = offset
    cosine = math.cos(rotation)
    sine = math.sin(rotation)
    a = cosine
    c = sine
    b = -sine
    d = cosine
    e = reference_east - a * local_x - c * local_y
    f = reference_north - b * local_x - d * local_y
    return {
        "a": a,
        "b": b,
        "c": c,
        "d": d,
        "e": e,
        "f": f,
    }


__all__ = [
    "georeference_distance_m",
    "georeference_from_compressed_map_detail",
    "georeference_from_plain_map_detail",
    "georeference_is_valid",
    "local_to_site_affine",
    "local_xy_to_wgs84",
    "offset_wgs84",
    "update_georeference",
    "validate_georeference",
    "wgs84_offset_m",
]
