"""Align European mower map references with the static ETRS89 cartographic frame.

Navimow map/localization coordinates observed on H2 and i1 behave like a current
GNSS/ITRF-like dynamic frame, while European cartographic datasets are fixed to
ETRS89/ETRF. EPSG:8366 defines the time-dependent ITRF2014 -> ETRF2014
position-vector transformation. This module applies only the resulting small
horizontal translation to map display georeferences; mower local X/Y, rotation,
scale and the native GPS device_tracker remain untouched.

The vendor does not declare its absolute GNSS CRS, so this remains an explicit
field-test assumption. X3 maps with their vendor RTK_anchor/NRTK-LRTK path are
excluded because that vendor frame correction already owns their translation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import time
from typing import Any, Callable

from . import georeference as _georeference
from . import georeference_static_anchor_semantics as _static

_EPSG_OPERATION = 8366
_SOURCE_FRAME = "ITRF2014-like dynamic GNSS (vendor assumption)"
_TARGET_FRAME = "ETRF2014 / ETRS89 cartographic"
_REFERENCE_EPOCH = 1989.0
_DRX_MAS_PER_YEAR = 0.085
_DRY_MAS_PER_YEAR = 0.531
_DRZ_MAS_PER_YEAR = -0.770
_MAS_TO_RAD = math.pi / (180.0 * 3600.0 * 1000.0)
_GRS80_A_M = 6378137.0
_GRS80_INV_F = 298.257222101
_EUROPE_BBOX = (33.26, -16.10, 84.73, 38.01)  # EPSG:8366 area of use.
_STATIC_SOURCE = "vendor_map_static_fit"
_X3_SOURCE = "x3_rtk_anchor"
_PROBE_MARKER = "etrs89_cartographic_v1"

_INSTALLED = False
_ORIGINAL_FROM_GEOMETRY: Callable[[Any], dict[str, Any] | None] | None = None
_ORIGINAL_UPDATE: Callable[[Any, Any], dict[str, Any] | None] | None = None
_ORIGINAL_LOAD: Callable[..., Any] | None = None


def _decimal_year_from_timestamp(value: Any) -> float | None:
    parsed = _georeference._float(value)  # noqa: SLF001
    if parsed is None:
        return None
    if parsed > 10_000_000_000.0:
        parsed /= 1000.0
    try:
        instant = datetime.fromtimestamp(parsed, tz=timezone.utc)
        start = datetime(instant.year, 1, 1, tzinfo=timezone.utc)
        end = datetime(instant.year + 1, 1, 1, tzinfo=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None
    fraction = (instant - start).total_seconds() / (end - start).total_seconds()
    return instant.year + fraction


def _current_decimal_year() -> float:
    return _decimal_year_from_timestamp(time.time()) or 2026.0


def _coordinate_epoch(
    geometry: dict[str, Any], location: Any, active: dict[str, Any]
) -> tuple[float, str]:
    """Choose the epoch that best matches the coordinates owning translation."""
    if active.get("source") == _STATIC_SOURCE:
        epoch = _decimal_year_from_timestamp(geometry.get("edit_time"))
        if epoch is not None:
            return epoch, "map_edit_time"

    calibration = geometry.get("_georeference_calibration")
    if isinstance(calibration, dict):
        samples = [
            item
            for item in calibration.get("samples") or []
            if isinstance(item, dict)
        ]
        epochs = [
            value
            for item in samples
            if (value := _decimal_year_from_timestamp(item.get("report_time")))
            is not None
        ]
        if epochs:
            return max(epochs), "latest_georeference_sample"

    if isinstance(location, dict):
        epoch = _decimal_year_from_timestamp(location.get("report_time"))
        if epoch is not None:
            return epoch, "private_cloud_report_time"

    epoch = _decimal_year_from_timestamp(geometry.get("edit_time"))
    if epoch is not None:
        return epoch, "map_edit_time_fallback"
    return _current_decimal_year(), "current_utc_fallback"


def _in_epsg8366_area(latitude: float, longitude: float) -> bool:
    south, west, north, east = _EUROPE_BBOX
    return south <= latitude <= north and west <= longitude <= east


def _itrf2014_to_etrf2014_offset(
    latitude: Any, longitude: Any, epoch: Any
) -> dict[str, float] | None:
    """Return EPSG:8366 displacement as local East/North/Up metres.

    EPSG:8366 has zero translations/scale and only rotation rates at epoch 1989.
    Applying the small position-vector rotation to a GRS80 geocentric point and
    resolving its delta into local ENU is sufficient for this sub-metre display
    correction and avoids adding a heavy PROJ dependency to Home Assistant.
    """
    lat = _georeference._float(latitude)  # noqa: SLF001
    lon = _georeference._float(longitude)  # noqa: SLF001
    observation_epoch = _georeference._float(epoch)  # noqa: SLF001
    if None in (lat, lon, observation_epoch):
        return None
    assert lat is not None and lon is not None and observation_epoch is not None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    if not (1989.0 <= observation_epoch <= 2100.0):
        return None

    phi = math.radians(lat)
    lam = math.radians(lon)
    flattening = 1.0 / _GRS80_INV_F
    eccentricity_sq = flattening * (2.0 - flattening)
    sin_phi = math.sin(phi)
    cos_phi = math.cos(phi)
    sin_lam = math.sin(lam)
    cos_lam = math.cos(lam)
    prime_vertical = _GRS80_A_M / math.sqrt(
        1.0 - eccentricity_sq * sin_phi * sin_phi
    )

    x = prime_vertical * cos_phi * cos_lam
    y = prime_vertical * cos_phi * sin_lam
    z = prime_vertical * (1.0 - eccentricity_sq) * sin_phi

    years = observation_epoch - _REFERENCE_EPOCH
    rx = _DRX_MAS_PER_YEAR * years * _MAS_TO_RAD
    ry = _DRY_MAS_PER_YEAR * years * _MAS_TO_RAD
    rz = _DRZ_MAS_PER_YEAR * years * _MAS_TO_RAD

    # EPSG method 1053: time-dependent position-vector transformation.
    x2 = x - rz * y + ry * z
    y2 = rz * x + y - rx * z
    z2 = -ry * x + rx * y + z
    dx, dy, dz = x2 - x, y2 - y, z2 - z

    east_m = -sin_lam * dx + cos_lam * dy
    north_m = (
        -sin_phi * cos_lam * dx
        - sin_phi * sin_lam * dy
        + cos_phi * dz
    )
    up_m = cos_phi * cos_lam * dx + cos_phi * sin_lam * dy + sin_phi * dz
    distance_m = math.hypot(east_m, north_m)
    return {
        "east_m": east_m,
        "north_m": north_m,
        "up_m": up_m,
        "distance_m": distance_m,
        "bearing_deg_from_north": (
            math.degrees(math.atan2(east_m, north_m)) + 360.0
        )
        % 360.0,
    }


def _transform_complete(value: Any) -> bool:
    """Return whether a vendor transform has the fields needed for projection.

    A raw vendor bootstrap transform intentionally has no ``status=validated``
    yet, so ``georeference_is_valid`` is too strict for identifying its frame
    semantics here.
    """
    if not isinstance(value, dict):
        return False
    reference = value.get("reference") or {}
    return all(
        _georeference._float(item) is not None  # noqa: SLF001
        for item in (
            reference.get("local_x"),
            reference.get("local_y"),
            reference.get("latitude"),
            reference.get("longitude"),
            value.get("rotation_rad"),
        )
    )


def _vendor_support_kind(
    geometry: dict[str, Any], active: dict[str, Any]
) -> str | None:
    """Return why this map has a trustworthy vendor-owned local frame."""
    source = active.get("source")
    if source == _X3_SOURCE:
        return None
    if source == _STATIC_SOURCE:
        return "static_vendor_ties"

    vendor = geometry.get("_vendor_georeference")
    if not isinstance(vendor, dict):
        return None
    vendor_source = vendor.get("source")
    if vendor_source == _X3_SOURCE:
        return None
    if vendor_source == _STATIC_SOURCE or (
        (vendor.get("static_validation") or {}).get("valid") is True
    ):
        return "static_vendor_ties"
    if vendor_source == "vendor_map_detail" and _transform_complete(vendor):
        return "explicit_vendor_map_north_offset"
    return None


def _decorate_vendor_metadata(geom: Any) -> dict[str, Any] | None:
    """Retain normalized raw-map evidence needed after geometry reduction."""
    if _ORIGINAL_FROM_GEOMETRY is None:
        return None
    original = _ORIGINAL_FROM_GEOMETRY(geom)
    if not isinstance(original, dict) or not isinstance(geom, dict):
        return original

    decorated = deepcopy(original)
    rtk = geom.get("rtk") if isinstance(geom.get("rtk"), dict) else {}
    raw_bias = rtk.get("bias") if isinstance(rtk, dict) else None
    raw_pile = rtk.get("pile") if isinstance(rtk, dict) else None
    anchor = _static._rtk_anchor(geom)  # noqa: SLF001
    center_local = _georeference._xy(geom.get("map_circle_center"))  # noqa: SLF001
    decorated["vendor_metadata"] = {
        "map_north_offset_present": (
            _georeference._float(geom.get("map_north_offset")) is not None  # noqa: SLF001
        ),
        "origin_gps_present": (
            _georeference._gps(geom.get("origin_gps")) is not None  # noqa: SLF001
        ),
        "center_gps_present": (
            _georeference._gps(geom.get("center_gps")) is not None  # noqa: SLF001
        ),
        "south_west_gps_present": (
            _georeference._gps(geom.get("sw_gps")) is not None  # noqa: SLF001
        ),
        "north_east_gps_present": (
            _georeference._gps(geom.get("ne_gps")) is not None  # noqa: SLF001
        ),
        "rtk_anchor_present": anchor is not None,
        "rtk_bias_present": (
            isinstance(raw_bias, str) and "nrtk_lrtk_bias" in raw_bias
        ),
        "rtk_pile_present": isinstance(raw_pile, str) and "LRTK" in raw_pile,
        "map_circle_center_local": (
            list(center_local) if center_local is not None else None
        ),
        "map_width_m": _georeference._float(geom.get("map_width")),  # noqa: SLF001
        "map_height_m": _georeference._float(geom.get("map_height")),  # noqa: SLF001
    }
    if anchor is not None:
        latitude, longitude, altitude = anchor
        decorated["rtk_anchor"] = {
            "latitude": latitude,
            "longitude": longitude,
            "altitude_m": altitude,
        }
    return decorated


def _apply_cartographic_frame(
    geometry: Any,
    active: Any,
    location: Any,
    *,
    epoch_override: float | None = None,
) -> dict[str, Any] | None:
    if not isinstance(active, dict) or not _georeference.georeference_is_valid(active):
        return active
    if not isinstance(geometry, dict):
        return active

    support_kind = _vendor_support_kind(geometry, active)
    if active.get("source") == _X3_SOURCE:
        updated = deepcopy(active)
        updated["cartographic_frame"] = {
            "experimental": True,
            "applied": False,
            "reason": "x3_vendor_rtk_frame_owns_translation",
            "epsg_operation": _EPSG_OPERATION,
        }
        geometry[_PROBE_MARKER] = True
        geometry["georeference"] = updated
        return updated
    if support_kind is None:
        return active

    reference = active.get("reference") or {}
    latitude = _georeference._float(reference.get("latitude"))  # noqa: SLF001
    longitude = _georeference._float(reference.get("longitude"))  # noqa: SLF001
    if latitude is None or longitude is None:
        return active

    if epoch_override is None:
        epoch, epoch_source = _coordinate_epoch(geometry, location, active)
    else:
        epoch, epoch_source = float(epoch_override), "test_override"

    updated = deepcopy(active)
    frame: dict[str, Any] = {
        "experimental": True,
        "epsg_operation": _EPSG_OPERATION,
        "method": "time_dependent_position_vector_geocentric",
        "source_frame_assumption": _SOURCE_FRAME,
        "target_frame": _TARGET_FRAME,
        "reference_epoch": _REFERENCE_EPOCH,
        "coordinate_epoch": round(epoch, 6),
        "coordinate_epoch_source": epoch_source,
        "support_kind": support_kind,
        "rotation_rate_mas_per_year": [
            _DRX_MAS_PER_YEAR,
            _DRY_MAS_PER_YEAR,
            _DRZ_MAS_PER_YEAR,
        ],
    }
    if not _in_epsg8366_area(latitude, longitude):
        frame.update({"applied": False, "reason": "outside_epsg8366_area"})
        updated["cartographic_frame"] = frame
        geometry[_PROBE_MARKER] = True
        geometry["georeference"] = updated
        return updated

    correction = _itrf2014_to_etrf2014_offset(latitude, longitude, epoch)
    if correction is None:
        frame.update({"applied": False, "reason": "epsg8366_offset_failed"})
        updated["cartographic_frame"] = frame
        geometry[_PROBE_MARKER] = True
        geometry["georeference"] = updated
        return updated

    corrected = _georeference.offset_wgs84(
        latitude,
        longitude,
        correction["east_m"],
        correction["north_m"],
    )
    if corrected is None:
        frame.update({"applied": False, "reason": "reference_offset_failed"})
        updated["cartographic_frame"] = frame
        geometry[_PROBE_MARKER] = True
        geometry["georeference"] = updated
        return updated

    corrected_latitude, corrected_longitude = corrected
    updated_reference = dict(reference)
    updated_reference["latitude"] = corrected_latitude
    updated_reference["longitude"] = corrected_longitude
    updated["reference"] = updated_reference
    frame.update(
        {
            "applied": True,
            "east_m": round(correction["east_m"], 3),
            "north_m": round(correction["north_m"], 3),
            "up_m": round(correction["up_m"], 3),
            "distance_m": round(correction["distance_m"], 3),
            "bearing_deg_from_north": round(
                correction["bearing_deg_from_north"], 2
            ),
            "translation_only": True,
        }
    )
    updated["cartographic_frame"] = frame
    geometry[_PROBE_MARKER] = True
    geometry["georeference"] = updated
    return updated


def _update(map_geometry: Any, location: Any) -> dict[str, Any] | None:
    if _ORIGINAL_UPDATE is None:
        return None
    active = _ORIGINAL_UPDATE(map_geometry, location)
    return _apply_cartographic_frame(map_geometry, active, location)


async def _load_persistent_state(self: Any) -> None:
    """Refresh beta15 map caches once to retain normalized raw reference evidence."""
    if _ORIGINAL_LOAD is None:
        return
    await _ORIGINAL_LOAD(self)
    geometry = getattr(self, "_map_geometry", None)
    if not isinstance(geometry, dict) or geometry.get(_PROBE_MARKER):
        return
    active = geometry.get("georeference")
    vendor = geometry.get("_vendor_georeference")
    relevant = (
        isinstance(active, dict)
        and active.get("source")
        in {_STATIC_SOURCE, "cloud_location_fit", "vendor_map_detail"}
    ) or (
        isinstance(vendor, dict)
        and vendor.get("source") in {_STATIC_SOURCE, "vendor_map_detail"}
    )
    if relevant:
        self._map_cache_key = None  # noqa: SLF001


def install_georeference_cartographic_semantics() -> None:
    """Install cartographic correction after X3 vendor-frame handling."""
    global _INSTALLED, _ORIGINAL_FROM_GEOMETRY, _ORIGINAL_UPDATE, _ORIGINAL_LOAD
    if _INSTALLED:
        return

    from . import coordinator_semantics as _coordinator_semantics

    _ORIGINAL_FROM_GEOMETRY = _georeference.georeference_from_geometry
    _ORIGINAL_UPDATE = _georeference.update_georeference
    _ORIGINAL_LOAD = (
        _coordinator_semantics.NavimowCoordinator.async_load_persistent_state
    )

    _georeference.georeference_from_geometry = _decorate_vendor_metadata
    _georeference.update_georeference = _update
    _coordinator_semantics.update_georeference = _update
    _coordinator_semantics.NavimowCoordinator.async_load_persistent_state = (
        _load_persistent_state
    )
    _INSTALLED = True


__all__ = ["install_georeference_cartographic_semantics"]
