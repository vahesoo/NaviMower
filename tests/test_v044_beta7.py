"""Regression tests for 0.4.4-beta7 staged georeference refinement."""
from __future__ import annotations

from custom_components.navimower.georeference import offset_wgs84
from custom_components.navimower.georeference_semantics import stable_update_georeference


def _sample(x: float, report_time: int) -> dict:
    point = offset_wgs84(58.0, 24.0, x, 0.0)
    assert point is not None
    return {
        "x": x,
        "y": 0.0,
        "latitude": point[0],
        "longitude": point[1],
        "report_time": report_time,
    }


def _location(x: float, report_time: int) -> dict:
    sample = _sample(x, report_time)
    return {
        "posture_x": sample["x"],
        "posture_y": sample["y"],
        "latitude": sample["latitude"],
        "longitude": sample["longitude"],
        "report_time": report_time,
    }


def _geometry_with_five_sample_fit() -> dict:
    samples = [_sample(float(index * 3), index + 1) for index in range(5)]
    fit = {
        "schema_version": 2,
        "source": "cloud_location_fit",
        "status": "validated",
        "reference": {
            "local_x": 6.0,
            "local_y": 0.0,
            "latitude": offset_wgs84(58.0, 24.0, 6.0, 0.0)[0],
            "longitude": offset_wgs84(58.0, 24.0, 6.0, 0.0)[1],
        },
        "rotation_rad": 0.0,
        "calibration": {
            "sample_count": 5,
            "inlier_count": 5,
            "rejected_count": 0,
            "baseline_m": 12.0,
            "rms_error_m": 0.0,
            "max_error_m": 0.0,
            "observed_scale": 1.0,
            "min_samples": 5,
            "min_baseline_m": 10.0,
        },
    }
    return {
        "revision": "map-staged",
        "_georeference_calibration": {
            "map_revision": "map-staged",
            "samples": samples,
            "fit": fit,
            "mismatch_count": 0,
        },
        "georeference": dict(fit),
    }


def test_refinement_runs_at_8_to_10_and_again_from_12_samples() -> None:
    geometry = _geometry_with_five_sample_fit()

    result = stable_update_georeference(geometry, _location(15.0, 6))
    assert result is not None
    assert result["refinement_stage"] == "provisional"
    assert result["calibration"]["refinement_sample_count"] == 6
    assert result["calibration"].get("last_refinement_sample_count") is None

    result = stable_update_georeference(geometry, _location(18.0, 7))
    assert result["refinement_stage"] == "provisional"
    assert result["calibration"]["refinement_sample_count"] == 7

    result = stable_update_georeference(geometry, _location(21.0, 8))
    assert result["refinement_stage"] == "refined"
    assert result["calibration"]["last_refinement_result"] == "accepted"
    assert result["calibration"]["last_refinement_sample_count"] == 8

    result = stable_update_georeference(geometry, _location(24.0, 9))
    assert result["calibration"]["last_refinement_sample_count"] == 9

    result = stable_update_georeference(geometry, _location(27.0, 10))
    assert result["calibration"]["last_refinement_sample_count"] == 10

    result = stable_update_georeference(geometry, _location(30.0, 11))
    assert result["refinement_stage"] == "refined"
    assert result["calibration"]["refinement_sample_count"] == 11
    assert result["calibration"]["last_refinement_sample_count"] == 10

    result = stable_update_georeference(geometry, _location(33.0, 12))
    assert result["refinement_stage"] == "high_confidence"
    assert result["calibration"]["last_refinement_sample_count"] == 12

    result = stable_update_georeference(geometry, _location(36.0, 13))
    assert result["refinement_stage"] == "high_confidence"
    assert result["calibration"]["last_refinement_sample_count"] == 13


def test_stationary_gps_drift_does_not_create_refinement_samples() -> None:
    geometry = _geometry_with_five_sample_fit()
    drifted = offset_wgs84(58.0, 24.0, 10.0, 0.0)
    assert drifted is not None

    result = stable_update_georeference(
        geometry,
        {
            "posture_x": 12.0,
            "posture_y": 0.0,
            "latitude": drifted[0],
            "longitude": drifted[1],
            "report_time": 99,
        },
    )

    assert result is not None
    assert result["status"] == "validated"
    assert result["refinement_stage"] == "provisional"
    assert result["calibration"]["refinement_sample_count"] == 5
    assert len(geometry["_georeference_calibration"]["samples"]) == 5
