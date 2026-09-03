"""Reject vendor zero-pose sentinels from georeference learning/validation.

Some mower firmwares report a docked/reset placeholder of local ``(0, 0, 0)``
while retaining the previous geographic position. That is not a paired
local-X/Y + GPS observation and must therefore never be allowed to invalidate a
static map transform or enter the learned cloud fit.

Detection is evidence-driven rather than model-driven: the current local pose
must be fully zeroed, the retained previous local pose must be materially away
from the origin, and current/previous GPS must still describe effectively the
same point.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Callable

from . import georeference as _georeference

_VENDOR_MAP_DETAIL_SOURCE = "vendor_map_detail"
_ZERO_POSE_EPS = 1e-6
_MIN_PREVIOUS_LOCAL_DISTANCE_M = 0.5
_MAX_RETAINED_GPS_DISTANCE_M = 0.10

_INSTALLED = False
_ORIGINAL_CURRENT_SAMPLE: Callable[[Any], dict[str, Any] | None] | None = None
_ORIGINAL_VALIDATE: Callable[..., dict[str, Any] | None] | None = None
_ORIGINAL_UPDATE: Callable[[Any, Any], dict[str, Any] | None] | None = None


def zero_pose_sentinel(location: Any) -> bool:
    """Return whether one location payload is an inconsistent zero-pose sentinel."""
    if not isinstance(location, dict):
        return False

    x = _georeference._float(location.get("posture_x"))  # noqa: SLF001
    y = _georeference._float(location.get("posture_y"))  # noqa: SLF001
    theta = _georeference._float(location.get("posture_theta"))  # noqa: SLF001
    last_x = _georeference._float(location.get("last_posture_x"))  # noqa: SLF001
    last_y = _georeference._float(location.get("last_posture_y"))  # noqa: SLF001
    latitude = _georeference._float(location.get("latitude"))  # noqa: SLF001
    longitude = _georeference._float(location.get("longitude"))  # noqa: SLF001
    last_latitude = _georeference._float(location.get("last_latitude"))  # noqa: SLF001
    last_longitude = _georeference._float(location.get("last_longitude"))  # noqa: SLF001

    if None in (
        x,
        y,
        theta,
        last_x,
        last_y,
        latitude,
        longitude,
        last_latitude,
        last_longitude,
    ):
        return False
    assert x is not None and y is not None and theta is not None
    assert last_x is not None and last_y is not None
    assert latitude is not None and longitude is not None
    assert last_latitude is not None and last_longitude is not None

    if not (
        abs(x) <= _ZERO_POSE_EPS
        and abs(y) <= _ZERO_POSE_EPS
        and abs(theta) <= _ZERO_POSE_EPS
    ):
        return False
    if math.hypot(last_x, last_y) < _MIN_PREVIOUS_LOCAL_DISTANCE_M:
        return False

    retained = _georeference.wgs84_offset_m(
        last_latitude,
        last_longitude,
        latitude,
        longitude,
    )
    if retained is None:
        return False
    return math.hypot(retained[0], retained[1]) <= _MAX_RETAINED_GPS_DISTANCE_M


def _sentinel_validation(location: Any) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "valid": None,
        "reason": "zero_pose_sentinel",
        "report_time": location.get("report_time") if isinstance(location, dict) else None,
    }


def _sample_matches_retained_sentinel(sample: Any, location: Any) -> bool:
    """Match one previously persisted poisoned sample to a confirmed sentinel."""
    if not isinstance(sample, dict) or not isinstance(location, dict):
        return False
    x = _georeference._float(sample.get("x"))  # noqa: SLF001
    y = _georeference._float(sample.get("y"))  # noqa: SLF001
    latitude = _georeference._float(sample.get("latitude"))  # noqa: SLF001
    longitude = _georeference._float(sample.get("longitude"))  # noqa: SLF001
    retained_latitude = _georeference._float(location.get("latitude"))  # noqa: SLF001
    retained_longitude = _georeference._float(location.get("longitude"))  # noqa: SLF001
    if None in (x, y, latitude, longitude, retained_latitude, retained_longitude):
        return False
    assert x is not None and y is not None
    assert latitude is not None and longitude is not None
    assert retained_latitude is not None and retained_longitude is not None
    if abs(x) > _ZERO_POSE_EPS or abs(y) > _ZERO_POSE_EPS:
        return False
    offset = _georeference.wgs84_offset_m(
        retained_latitude,
        retained_longitude,
        latitude,
        longitude,
    )
    return (
        offset is not None
        and math.hypot(offset[0], offset[1]) <= _MAX_RETAINED_GPS_DISTANCE_M
    )


def _purge_persisted_sentinel_samples(
    map_geometry: Any,
    location: Any,
) -> int:
    """Remove stale zero-pose poison retained before this guard was installed."""
    if not isinstance(map_geometry, dict) or not zero_pose_sentinel(location):
        return 0
    calibration = map_geometry.get("_georeference_calibration")
    if not isinstance(calibration, dict):
        return 0
    samples = [
        dict(item) for item in calibration.get("samples") or [] if isinstance(item, dict)
    ]
    if not samples:
        return 0
    retained = [
        sample
        for sample in samples
        if not _sample_matches_retained_sentinel(sample, location)
    ]
    removed = len(samples) - len(retained)
    if removed <= 0:
        return 0
    calibration["samples"] = retained
    calibration["zero_pose_sentinel_purged_samples"] = (
        int(calibration.get("zero_pose_sentinel_purged_samples") or 0) + removed
    )
    calibration["last_zero_pose_sentinel_purge_count"] = removed
    return removed


def _current_location_sample(location: Any) -> dict[str, Any] | None:
    if zero_pose_sentinel(location):
        return None
    if _ORIGINAL_CURRENT_SAMPLE is None:
        return None
    return _ORIGINAL_CURRENT_SAMPLE(location)


def _validate_georeference(
    georeference: Any,
    location: Any,
    *,
    limit_m: float = 2.0,
) -> dict[str, Any] | None:
    if zero_pose_sentinel(location):
        if not isinstance(georeference, dict):
            return None
        result = deepcopy(georeference)
        result["validation"] = _sentinel_validation(location)
        return result
    if _ORIGINAL_VALIDATE is None:
        return None
    return _ORIGINAL_VALIDATE(georeference, location, limit_m=limit_m)


def _complete_vendor_map_detail(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("source") != _VENDOR_MAP_DETAIL_SOURCE:
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


def _recover_vendor_map_detail(
    map_geometry: dict[str, Any],
    active: Any,
    location: Any,
) -> dict[str, Any] | None:
    """Recover an explicit vendor transform when only sentinel validation blocked it."""
    if not zero_pose_sentinel(location):
        return active if isinstance(active, dict) else None
    if _georeference.georeference_is_valid(active):
        return active

    vendor = map_geometry.get("_vendor_georeference")
    if not _complete_vendor_map_detail(vendor):
        return active if isinstance(active, dict) else None
    assert isinstance(vendor, dict)

    calibration = map_geometry.get("_georeference_calibration")
    calibration = calibration if isinstance(calibration, dict) else {}
    recovered = deepcopy(vendor)
    recovered.update(
        {
            "schema_version": 2,
            "source": _VENDOR_MAP_DETAIL_SOURCE,
            "status": "validated",
            "map_revision": str(map_geometry.get("revision") or ""),
            "anchor_policy": "vendor_map_detail_zero_pose_fallback",
            "calibration": _georeference._calibration_summary(calibration),  # noqa: SLF001
            "validation": _sentinel_validation(location),
        }
    )
    map_geometry["georeference"] = recovered
    return recovered


def _update(map_geometry: Any, location: Any) -> dict[str, Any] | None:
    if _ORIGINAL_UPDATE is None:
        return None
    _purge_persisted_sentinel_samples(map_geometry, location)
    active = _ORIGINAL_UPDATE(map_geometry, location)
    if not isinstance(map_geometry, dict):
        return active
    return _recover_vendor_map_detail(map_geometry, active, location)


def install_georeference_pose_semantics() -> None:
    """Install zero-pose rejection after static/X3 ownership is established."""
    global _INSTALLED, _ORIGINAL_CURRENT_SAMPLE, _ORIGINAL_VALIDATE, _ORIGINAL_UPDATE
    if _INSTALLED:
        return

    from . import coordinator_semantics as _coordinator_semantics

    _ORIGINAL_CURRENT_SAMPLE = _georeference._current_location_sample  # noqa: SLF001
    _ORIGINAL_VALIDATE = _georeference.validate_georeference
    _ORIGINAL_UPDATE = _georeference.update_georeference

    _georeference._current_location_sample = _current_location_sample  # noqa: SLF001
    _georeference.validate_georeference = _validate_georeference
    _georeference.update_georeference = _update
    _coordinator_semantics.update_georeference = _update
    _INSTALLED = True


__all__ = ["install_georeference_pose_semantics", "zero_pose_sentinel"]
