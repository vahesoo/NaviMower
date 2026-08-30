"""Regression tests for 0.4.4-beta9 adaptive spatial georeference refinement."""
from __future__ import annotations

from custom_components.navimower import georeference as geo
from custom_components.navimower.georeference_semantics import stable_update_georeference


def _sample(x: float, y: float, report_time: int) -> dict:
    point = geo.offset_wgs84(58.0, 24.0, x, y)
    assert point is not None
    return {
        "x": x,
        "y": y,
        "latitude": point[0],
        "longitude": point[1],
        "report_time": report_time,
    }


def _location(x: float, y: float, report_time: int) -> dict:
    sample = _sample(x, y, report_time)
    return {
        "posture_x": x,
        "posture_y": y,
        "latitude": sample["latitude"],
        "longitude": sample["longitude"],
        "report_time": report_time,
    }


def _full_geometry() -> dict:
    # 24 well-separated points, intentionally confined to a 20 x 20 m patch.
    samples = []
    report_time = 1
    for y in (0.0, 4.0, 8.0, 12.0):
        for x in (0.0, 4.0, 8.0, 12.0, 16.0, 20.0):
            samples.append(_sample(x, y, report_time))
            report_time += 1
    fit = geo._fit_samples(samples)  # noqa: SLF001
    assert fit is not None and fit["status"] == "validated"
    return {
        "revision": "map-adaptive",
        "_georeference_calibration": {
            "map_revision": "map-adaptive",
            "samples": samples,
            "fit": fit,
            "mismatch_count": 0,
            "refinement_policy": "anchored_milestones_v2",
            "refinement_locked": True,
            "last_refinement_result": "accepted",
            "last_refinement_sample_count": 24,
        },
        "georeference": dict(fit),
    }


def test_beta8_full_set_migrates_and_accepts_farther_spatial_point() -> None:
    geometry = _full_geometry()
    before_samples = list(geometry["_georeference_calibration"]["samples"])
    before_baseline = geo._baseline_m(before_samples)  # noqa: SLF001

    result = stable_update_georeference(geometry, _location(42.0, 18.0, 100))

    assert result is not None
    calibration = result["calibration"]
    assert calibration["refinement_policy"] == "adaptive_spatial_v3"
    assert calibration["refinement_sample_count"] == 24
    assert calibration["refinement_locked"] is False
    assert calibration["last_adaptive_result"] == "accepted"
    assert calibration["adaptive_replacement_count"] == 1
    assert calibration["spatial_baseline_m"] > before_baseline
    assert len(geometry["_georeference_calibration"]["samples"]) == 24
    assert any(item["report_time"] == 100 for item in geometry["_georeference_calibration"]["samples"])


def test_full_set_rejects_point_without_material_coverage_gain() -> None:
    geometry = _full_geometry()
    before = [dict(item) for item in geometry["_georeference_calibration"]["samples"]]

    result = stable_update_georeference(geometry, _location(10.0, 10.0, 101))

    assert result is not None
    calibration = result["calibration"]
    assert calibration["refinement_policy"] == "adaptive_spatial_v3"
    assert calibration["adaptive_replacement_count"] == 0
    assert calibration["last_adaptive_result"] in {
        "no_material_coverage_gain",
        "duplicate",
    }
    assert geometry["_georeference_calibration"]["samples"] == before


def test_adaptive_replacement_keeps_fit_healthy() -> None:
    geometry = _full_geometry()

    result = stable_update_georeference(geometry, _location(45.0, -10.0, 102))

    assert result is not None
    assert result["status"] == "validated"
    assert result["calibration"]["last_adaptive_result"] == "accepted"
    assert result["calibration"]["rms_error_m"] < 0.05
    assert result["calibration"]["max_error_m"] < 0.10
