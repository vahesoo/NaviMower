"""Regression tests for Navimower 0.4.4-beta12 static map georeference anchors."""
from __future__ import annotations

import json
import math
from pathlib import Path

from custom_components.navimower.georeference_static_anchor_semantics import (
    _static_map_georeference,
)

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"

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

_X390 = {
    "map_circle_center": [-36.297842, -38.782747],
    "center_gps": [26.861538, 59.180225],
    "origin_gps": [26.862171, 59.180573],
    "ne_gps": [26.862541, 59.180889],
    "sw_gps": [26.860535, 59.179562],
    "map_north_offset": None,
    "map_width": 114.721649,
    "map_height": 147.822342,
}


def test_beta12_minimum_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    assert version.startswith("0.4.4-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 12
    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta12.md").read_text(
        encoding="utf-8"
    )
    for phrase in ("origin_gps", "static", "i108", "X390", "cloud"):
        assert phrase in notes


def test_i108_static_vendor_ties_form_a_high_quality_anchor() -> None:
    result = _static_map_georeference(_I108)
    assert result is not None
    assert result["source"] == "vendor_map_static_fit"
    assert result["status"] == "validated"
    assert result["anchor_policy"] == "static_map_primary"
    validation = result["static_validation"]
    assert validation["tie_count"] == 4
    assert validation["max_error_m"] < 0.25
    assert 0.99 < validation["observed_scale"] < 1.01
    assert abs(validation["rotation_deg"]) < 0.2


def test_x390_static_vendor_ties_form_a_high_quality_anchor() -> None:
    result = _static_map_georeference(_X390)
    assert result is not None
    assert result["source"] == "vendor_map_static_fit"
    validation = result["static_validation"]
    assert validation["max_error_m"] < 0.25
    assert 0.99 < validation["observed_scale"] < 1.01
    assert abs(validation["rotation_deg"]) < 0.2


def test_explicit_h2_rotation_stays_on_existing_vendor_path() -> None:
    geometry = {
        **_I108,
        "map_north_offset": 0.5977016091346741,
    }
    assert _static_map_georeference(geometry) is None


def test_runtime_order_keeps_static_anchor_after_learned_fit_before_diagnostics() -> None:
    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    learned = runtime.index("install_georeference_semantics()")
    static = runtime.index("install_georeference_static_anchor_semantics()")
    diagnostics = runtime.index("install_georeference_diagnostics_semantics()")
    assert learned < static < diagnostics


def test_static_anchor_does_not_depend_on_live_cloud_gps_for_placement() -> None:
    source = (COMPONENT / "georeference_static_anchor_semantics.py").read_text(
        encoding="utf-8"
    )
    assert 'geom.get("origin_gps")' in source
    assert 'geom.get("center_gps")' in source
    assert 'geom.get("sw_gps")' in source
    assert 'geom.get("ne_gps")' in source
    assert 'active["cloud_validation"]' in source
    assert "cloud_location_fit" in source
    assert "vendor_map_static_fit" in source
