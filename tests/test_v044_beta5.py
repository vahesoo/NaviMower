"""Regression tests for 0.4.4-beta5 capability and georeference hardening."""
from __future__ import annotations

from pathlib import Path

from custom_components.navimower import capability_semantics
from custom_components.navimower.georeference import offset_wgs84
from custom_components.navimower.georeference_semantics import stable_update_georeference
from custom_components.navimower.model_capabilities import (
    FAMILY_H1,
    FAMILY_H2,
    FAMILY_I1,
    FAMILY_I2_AWD,
    FAMILY_I2_LIDAR,
    FAMILY_TERRANOX,
    FAMILY_X3,
    FAMILY_X4,
    capability_profile,
    model_family,
)

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_model_family_matrix_covers_observed_generations() -> None:
    assert model_family("H1500", 20000002) == FAMILY_H1
    assert model_family("H215") == FAMILY_H2
    assert model_family("i108") == FAMILY_I1
    assert model_family("i208 AWD") == FAMILY_I2_AWD
    assert model_family("i215 LiDAR") == FAMILY_I2_LIDAR
    assert model_family("X390") == FAMILY_X3
    assert model_family("X420") == FAMILY_X4
    assert model_family("CM240M1") == FAMILY_TERRANOX

    i1 = capability_profile("i108")
    assert i1.cutting_height_adjustment == "manual"
    assert i1.cutting_height_range_mm == (20, 60)
    assert i1.cutting_height_readable is False
    assert i1.cutting_height_writable is False

    h2 = capability_profile("H215")
    assert h2.cutting_height_writable is True
    assert h2.terrain_adapt is True
    assert h2.edge_sense is True
    assert h2.grass_pattern_enhancement is False

    i2_awd = capability_profile("i208 AWD")
    assert i2_awd.traction_control is True
    assert i2_awd.grass_pattern_enhancement is False

    x4 = capability_profile("X420")
    assert x4.traction_control is True
    assert x4.grass_pattern_enhancement is True

    terranox = capability_profile("CM240M1")
    assert terranox.grass_pattern_enhancement is True
    assert terranox.cutting_height_writable is False


def test_i1_height_range_is_metadata_not_claimed_current_height() -> None:
    snapshot = {
        "model": "i108",
        "vehicle_type": 160000001,
        "cutting_height_supported": True,
        "settings": {
            "cut_height": 20,
            "cut_height_raw": 20,
            "cutting_height_supported": True,
        },
        "raw": {
            "device_info": {"mowingHeightList": [20, 25, 30, 35, 40, 45, 50, 55, 60]},
            "set_list": {
                "height": "20",
                "mode": "02",
                "grassPatternEnhancement": 0,
                "tcsSwitch": 0,
                "terrainAdaptSwitch": 0,
                "edgeSense": 0,
            },
        },
        "zone_details": [
            {
                "id": 1,
                "cutting_height_supported": True,
                "configured_height_mm": None,
                "cutting_height_mm": 20,
                "inherits_global_height": None,
            }
        ],
        "zone_states": [{"id": 1, "cutting_height_mm": 20}],
        "map": {"zones": [{"boundary": {"height_set": 316}}]},
        "sessions": [{"cutting_height_mm": 20}],
        "capabilities": {
            "schema_version": 1,
            "observed": {
                "cutting_height": {"supported": True},
                "terrain_settings": {"supported": True},
            },
        },
    }

    capability_semantics._strip_untrusted_i1_height(snapshot)  # noqa: SLF001
    hardened = capability_semantics._harden_capability_profile(  # noqa: SLF001
        snapshot, snapshot["capabilities"]
    )

    assert snapshot["cutting_height_supported"] is False
    assert snapshot["settings"]["cut_height"] is None
    assert snapshot["settings"]["cut_height_raw"] == 20
    assert snapshot["zone_details"][0]["cutting_height_mm"] is None
    assert snapshot["zone_states"][0]["cutting_height_mm"] is None
    assert "height_set" not in snapshot["map"]["zones"][0]["boundary"]
    assert snapshot["sessions"][0]["cutting_height_mm"] is None

    height = hardened["settings"]["cutting_height"]
    assert height["physical_range_mm"] == [20, 60]
    assert height["range_source"] == "device_info.mowingHeightList"
    assert height["reported_current_value"] == 20
    assert height["readable_current_value"] is False
    assert height["writable"] is False
    assert height["manual_current_value_pending_validation"] is True
    assert hardened["settings"]["grass_pattern_enhancement"]["reported"] is True
    assert hardened["settings"]["grass_pattern_enhancement"]["writable"] is False
    assert hardened["observed"]["terrain_settings"]["supported"] is None
    assert hardened["observed"]["cutting_height"]["supported"] is False


def test_battery_bounds_are_only_claimed_when_device_info_supplies_them() -> None:
    base = {
        "model": "i108",
        "vehicle_type": 160000001,
        "raw": {
            "set_list": {"returnBatteryLevel": 15, "chargingLimit": 100},
            "device_info": {"nonstandardVehicleConfig": {"batteryConfig": {}}},
        },
    }
    unbounded = capability_semantics._battery_limits_capability(base)  # noqa: SLF001
    assert unbounded["vendor_bounds_available"] is False
    assert unbounded["return_range_pct"] is None
    assert unbounded["charging_range_pct"] is None

    bounded = {
        **base,
        "raw": {
            "set_list": base["raw"]["set_list"],
            "device_info": {
                "nonstandardVehicleConfig": {
                    "batteryConfig": {
                        "returnBatteryLevelMin": 5,
                        "returnBatteryLevelMax": 20,
                        "chargingLimitMin": 70,
                        "chargingLimitMax": 100,
                    }
                }
            },
        },
    }
    exact = capability_semantics._battery_limits_capability(bounded)  # noqa: SLF001
    assert exact["vendor_bounds_available"] is True
    assert exact["return_range_pct"] == [5, 20]
    assert exact["charging_range_pct"] == [70, 100]


def _learned_map() -> dict:
    fit = {
        "schema_version": 2,
        "source": "cloud_location_fit",
        "status": "validated",
        "reference": {
            "local_x": 0.0,
            "local_y": 0.0,
            "latitude": 58.0,
            "longitude": 24.0,
        },
        "rotation_rad": 0.0,
        "calibration": {
            "sample_count": 6,
            "inlier_count": 6,
            "rejected_count": 0,
            "baseline_m": 12.0,
            "rms_error_m": 0.2,
            "max_error_m": 0.4,
            "observed_scale": 1.0,
            "min_samples": 5,
            "min_baseline_m": 10.0,
        },
    }
    return {
        "revision": "map-a",
        "_georeference_calibration": {
            "map_revision": "map-a",
            "samples": [
                {"x": 0.0, "y": 0.0, "latitude": 58.0, "longitude": 24.0, "report_time": 1}
            ],
            "fit": fit,
            "mismatch_count": 0,
        },
        "georeference": dict(fit),
    }


def test_validated_georeference_is_frozen_until_map_revision_changes() -> None:
    geometry = _learned_map()
    far = offset_wgs84(58.0, 24.0, 10.0, 0.0)
    assert far is not None

    for report_time in range(10, 15):
        result = stable_update_georeference(
            geometry,
            {
                "posture_x": 0.0,
                "posture_y": 0.0,
                "latitude": far[0],
                "longitude": far[1],
                "report_time": report_time,
            },
        )
        assert result is not None
        assert result["status"] == "validated"
        assert geometry["_georeference_calibration"]["fit"] is not None

    assert geometry["_georeference_calibration"]["mismatch_count"] >= 5

    geometry["revision"] = "map-b"
    changed = stable_update_georeference(
        geometry,
        {
            "posture_x": 0.0,
            "posture_y": 0.0,
            "latitude": far[0],
            "longitude": far[1],
            "report_time": 20,
        },
    )
    assert changed is not None
    assert changed["status"] == "learning"
    assert geometry["_georeference_calibration"]["map_revision"] == "map-b"
    assert geometry["_georeference_calibration"]["fit"] is None


def test_beta5_runtime_and_release_metadata() -> None:
    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    assert "install_capability_semantics()" in runtime
    assert "install_georeference_semantics()" in runtime

    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta5.md").read_text(
        encoding="utf-8"
    )
    assert "capability" in notes.lower()
    assert "map revision" in notes.lower()
