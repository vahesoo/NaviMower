"""Regression tests for Navimower 0.4.4-beta14 X3 RTK bias correction."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path

from custom_components.navimower import georeference as geo
from custom_components.navimower import georeference_static_anchor_semantics as static_sem
from custom_components.navimower import georeference_x3_bias_semantics as bias_sem

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


def _apply(geometry: dict, monkeypatch) -> dict:
    baseline = static_sem._static_map_georeference(geometry)
    assert baseline is not None
    monkeypatch.setattr(bias_sem, "_ORIGINAL_APPLY", static_sem._apply_x3_rtk_anchor)
    result = bias_sem._apply_x3_rtk_bias(
        geometry,
        baseline,
        (59.180573, 26.862171),
    )
    assert result is not None
    return result


def test_beta14_version_release_notes_and_runtime_order() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    assert version.startswith("0.4.4-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 14
    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta14.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "nrtk_lrtk_bias",
        "4.02 m",
        "RTK_anchor",
        "X3",
        "i1",
        "H2",
    ):
        assert phrase in notes

    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    static_index = runtime.index("install_georeference_static_anchor_semantics()")
    bias_index = runtime.index("install_georeference_x3_bias_semantics()")
    diagnostics_index = runtime.index("install_georeference_diagnostics_semantics()")
    assert static_index < bias_index < diagnostics_index


def test_x390_applies_full_refined_bias_in_swapped_map_axis_order(monkeypatch) -> None:
    result = _apply(_X390, monkeypatch)
    assert result["source"] == "x3_rtk_anchor"
    assert result["anchor_policy"] == "x3_rtk_anchor_bias_primary"

    anchor = result["rtk_anchor"]
    reference = result["reference"]
    offset = geo.wgs84_offset_m(
        anchor["latitude"],
        anchor["longitude"],
        reference["latitude"],
        reference["longitude"],
    )
    assert offset is not None
    east_m, north_m = offset
    assert math.isclose(east_m, -2.081, abs_tol=0.01)
    assert math.isclose(north_m, 3.437, abs_tol=0.01)
    assert math.isclose(math.hypot(east_m, north_m), 4.018, abs_tol=0.02)

    validation = result["rtk_validation"]
    assert validation["translation_source"] == "RTK_anchor+nrtk_lrtk_bias"
    correction = validation["bias_correction"]
    assert correction["applied"] is True
    assert correction["axis_mapping"] == "east=bias[1], north=bias[0]"
    assert correction["distance_m"] == 4.018
    assert 328.0 < correction["bearing_deg_from_north"] < 330.0
    assert validation["bias"]["calibration_flag"] is True
    assert validation["bias"]["refined"] is True
    assert validation["bias"]["usable"] is True

    # Translation changes only; beta13's static tie rotation remains intact.
    assert abs(result["static_validation"]["rotation_deg"]) < 0.2
    assert 0.99 < result["static_validation"]["observed_scale"] < 1.01


def test_unqualified_bias_falls_back_to_plain_rtk_anchor(monkeypatch) -> None:
    geometry = deepcopy(_X390)
    geometry["rtk"]["bias"] = geometry["rtk"]["bias"].replace(
        "nrtk_lrtk_bias_refined: 1",
        "nrtk_lrtk_bias_refined: 0",
    )
    result = _apply(geometry, monkeypatch)
    assert result["anchor_policy"] == "x3_rtk_anchor_primary"
    assert math.isclose(result["reference"]["latitude"], 59.18058352)
    assert math.isclose(result["reference"]["longitude"], 26.86226353)
    correction = result["rtk_validation"]["bias_correction"]
    assert correction["applied"] is False
    assert "bias_not_refined" in correction["reasons"]


def test_bias_std_guard_rejects_uncertain_calibration(monkeypatch) -> None:
    geometry = deepcopy(_X390)
    geometry["rtk"]["bias"] = geometry["rtk"]["bias"].replace(
        "nrtk_lrtk_bias_std: 0.015 0.007 0.019",
        "nrtk_lrtk_bias_std: 0.015 0.700 0.019",
    )
    result = _apply(geometry, monkeypatch)
    assert result["anchor_policy"] == "x3_rtk_anchor_primary"
    correction = result["rtk_validation"]["bias_correction"]
    assert correction["applied"] is False
    assert "bias_std_too_large" in correction["reasons"]


def test_beta14_forces_one_refresh_of_beta13_x3_cache() -> None:
    source = (COMPONENT / "georeference_x3_bias_semantics.py").read_text(
        encoding="utf-8"
    )
    assert '_PROBE_MARKER = "x3_rtk_bias_v1"' in source
    assert 'if source == _X3_RTK_SOURCE:' in source
    assert "self._map_cache_key = None" in source
