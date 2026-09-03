from __future__ import annotations

import json
from pathlib import Path

from custom_components.navimower import georeference_pose_semantics as pose

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "navimower" / "manifest.json"
RUNTIME = ROOT / "custom_components" / "navimower" / "runtime.py"
NOTES = ROOT / ".github" / "release-notes" / "0.4.4-beta24.md"


def _sentinel_location() -> dict[str, object]:
    return {
        "posture_x": 0.0,
        "posture_y": 0.0,
        "posture_theta": 0.0,
        "last_posture_x": 0.16,
        "last_posture_y": -2.756,
        "last_posture_theta": 3.1,
        "latitude": 58.0,
        "longitude": 24.0,
        "last_latitude": 58.0,
        "last_longitude": 24.0,
        "report_time": "123456",
    }


def _vendor_map_detail() -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": "vendor_map_detail",
        "reference": {
            "local_x": -6.5,
            "local_y": 9.3,
            "latitude": 58.0001,
            "longitude": 24.0001,
        },
        "rotation_rad": 0.5977,
        "origin": {"latitude": 58.0, "longitude": 24.0},
    }


def test_beta24_release_contract() -> None:
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["version"] == "0.4.4-beta24"
    notes = NOTES.read_text()
    for token in (
        "zero-pose",
        "vendor_map_detail",
        "OpenStreetMap",
        "Ortofoto",
        "model",
    ):
        assert token.lower() in notes.lower()


def test_zero_pose_sentinel_requires_inconsistent_retained_pose_and_gps() -> None:
    location = _sentinel_location()
    assert pose.zero_pose_sentinel(location) is True

    real_origin = dict(location)
    real_origin["last_posture_x"] = 0.1
    real_origin["last_posture_y"] = 0.1
    assert pose.zero_pose_sentinel(real_origin) is False

    moved_gps = dict(location)
    moved_gps["last_latitude"] = 58.0001
    assert pose.zero_pose_sentinel(moved_gps) is False

    nonzero_pose = dict(location)
    nonzero_pose["posture_y"] = -0.2
    assert pose.zero_pose_sentinel(nonzero_pose) is False


def test_sentinel_is_never_accepted_as_learning_sample(monkeypatch) -> None:
    monkeypatch.setattr(pose, "_ORIGINAL_CURRENT_SAMPLE", lambda _location: {"accepted": True})
    assert pose._current_location_sample(_sentinel_location()) is None  # noqa: SLF001

    real = _sentinel_location()
    real["posture_x"] = 1.0
    assert pose._current_location_sample(real) == {"accepted": True}  # noqa: SLF001


def test_sentinel_validation_is_unavailable_not_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        pose,
        "_ORIGINAL_VALIDATE",
        lambda georef, _location, *, limit_m=2.0: {
            **georef,
            "validation": {"status": "validated", "valid": True, "limit_m": limit_m},
        },
    )
    checked = pose._validate_georeference(  # noqa: SLF001
        _vendor_map_detail(),
        _sentinel_location(),
        limit_m=2.0,
    )
    assert checked is not None
    assert checked["validation"] == {
        "status": "unavailable",
        "valid": None,
        "reason": "zero_pose_sentinel",
        "report_time": "123456",
    }


def test_explicit_vendor_transform_recovers_when_sentinel_blocks_validation() -> None:
    vendor = _vendor_map_detail()
    geometry = {
        "revision": "map-revision",
        "_vendor_georeference": vendor,
        "_georeference_calibration": {
            "map_revision": "map-revision",
            "samples": [{"x": 0.1, "y": -2.7}],
            "fit": None,
            "mismatch_count": 0,
        },
    }
    active = {
        "schema_version": 2,
        "source": "cloud_location_fit",
        "status": "learning",
        "map_revision": "map-revision",
    }

    recovered = pose._recover_vendor_map_detail(  # noqa: SLF001
        geometry,
        active,
        _sentinel_location(),
    )
    assert recovered is not None
    assert recovered["source"] == "vendor_map_detail"
    assert recovered["status"] == "validated"
    assert recovered["anchor_policy"] == "vendor_map_detail_zero_pose_fallback"
    assert recovered["reference"] == vendor["reference"]
    assert recovered["rotation_rad"] == vendor["rotation_rad"]
    assert recovered["validation"]["reason"] == "zero_pose_sentinel"
    assert geometry["georeference"] is recovered


def test_valid_active_transform_is_not_replaced_by_sentinel_fallback() -> None:
    active = {
        "schema_version": 2,
        "source": "cloud_location_fit",
        "status": "validated",
        "reference": {
            "local_x": 1.0,
            "local_y": 2.0,
            "latitude": 58.0,
            "longitude": 24.0,
        },
        "rotation_rad": 0.1,
    }
    geometry = {
        "revision": "map-revision",
        "_vendor_georeference": _vendor_map_detail(),
    }
    result = pose._recover_vendor_map_detail(  # noqa: SLF001
        geometry,
        active,
        _sentinel_location(),
    )
    assert result is active
    assert result["source"] == "cloud_location_fit"


def test_pose_guard_runs_after_x3_ownership_before_translation_refinement() -> None:
    runtime = RUNTIME.read_text()
    x3 = runtime.index("install_georeference_x3_bias_semantics()")
    guard = runtime.index("install_georeference_pose_semantics()")
    refinement = runtime.index("install_georeference_translation_refinement_semantics()")
    geodesy_state = runtime.index("install_georeference_geodesy_state_semantics()")
    assert x3 < guard < refinement < geodesy_state
