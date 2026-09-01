"""Regression tests for Navimower 0.4.4-beta13 X3 RTK map anchoring."""
from __future__ import annotations

import json
import math
from pathlib import Path

from custom_components.navimower.georeference_static_anchor_semantics import (
    _static_map_georeference,
)

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"

_X390 = {
    "map_circle_center": [-36.297842, -38.782747],
    "center_gps": [26.861538, 59.180225],
    "origin_gps": [26.862171, 59.180573],
    "ne_gps": [26.862541, 59.180889],
    "sw_gps": [26.860535, 59.179562],
    "map_north_offset": None,
    "map_width": 114.721649,
    "map_height": 147.822342,
    "rtk": {
        "anchor": "RTK_mode: 1\nRTK_anchor: 59.18058352 26.86226353 100.49851990",
        "bias": "nrtk_lrtk_calibration_flag: true\nnrtk_lrtk_bias: 3.437 -2.081 -8.847\nnrtk_lrtk_bias_std: 0.015 0.007 0.019\nnrtk_lrtk_bias_refined: 1",
        "pile": "LRTK -0.31932 -1.01919 -3.14535 2.017",
    },
}

_I108 = {
    "map_circle_center": [-11.4679, 1.4973],
    "center_gps": [24.6380367, 58.3840637],
    "origin_gps": [24.6382313, 58.3840523],
    "ne_gps": [24.638504, 58.3843384],
    "sw_gps": [24.6375675, 58.3837929],
    "map_north_offset": None,
    "map_width": 54.7474,
    "map_height": 61.053,
}


def test_beta13_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.4-beta13"
    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta13.md").read_text(
        encoding="utf-8"
    )
    for phrase in ("RTK_anchor", "X3", "translation", "i1", "H2"):
        assert phrase in notes


def test_x390_uses_rtk_anchor_for_absolute_translation() -> None:
    result = _static_map_georeference(_X390)
    assert result is not None
    assert result["source"] == "x3_rtk_anchor"
    assert result["anchor_policy"] == "x3_rtk_anchor_primary"
    assert result["reference"]["local_x"] == 0.0
    assert result["reference"]["local_y"] == 0.0
    assert math.isclose(result["reference"]["latitude"], 59.18058352)
    assert math.isclose(result["reference"]["longitude"], 26.86226353)

    validation = result["rtk_validation"]
    shift = validation["map_origin_difference"]
    assert 5.0 < shift["distance_m"] < 5.8
    assert 70.0 < shift["bearing_deg_from_north"] < 85.0
    assert validation["pile_local_xy_interpreted"] == [-1.01919, -0.31932]
    assert validation["bias"]["refined"] is True

    # Rotation still comes from the internally consistent static map ties.
    assert abs(result["static_validation"]["rotation_deg"]) < 0.2
    assert 0.99 < result["static_validation"]["observed_scale"] < 1.01


def test_i1_without_rtk_metadata_keeps_static_map_anchor() -> None:
    result = _static_map_georeference(_I108)
    assert result is not None
    assert result["source"] == "vendor_map_static_fit"
    assert result["anchor_policy"] == "static_map_primary"
    assert "rtk_validation" not in result


def test_h2_explicit_rotation_never_enters_x3_rtk_path() -> None:
    geometry = {
        **_X390,
        "map_north_offset": 0.5977016091346741,
    }
    assert _static_map_georeference(geometry) is None


def test_x3_anchor_keeps_cloud_as_validation_not_translation() -> None:
    source = (COMPONENT / "georeference_static_anchor_semantics.py").read_text(
        encoding="utf-8"
    )
    assert '"translation_source": "RTK_anchor"' in source
    assert '"rotation_source": "vendor_static_tie_fit"' in source
    assert 'active["cloud_validation"]' in source
    assert '"x3_rtk_anchor"' in source
