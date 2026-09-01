"""Regression tests for Navimower 0.4.4-beta15 georeference diagnostics."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from custom_components.navimower import georeference as geo
from custom_components.navimower.georeference_reference_diagnostics import (
    reference_candidate_diagnostics,
)
from custom_components.navimower.georeference_static_anchor_semantics import (
    _static_map_georeference,
)

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"

_H215 = {
    "map_circle_center": [-6.547005460295729, 9.331472597744508],
    "center_gps": [24.638553619384766, 58.384281158447266],
    "origin_gps": [24.638547897338867, 58.38420104980469],
    "map_north_offset": 0.5977016091346741,
    "map_width": 90.0,
    "map_height": 80.0,
    "station": {"x": -0.3309, "y": -2.7012},
    "rtk": {
        "anchor": "RTK_mode: 1\nRTK_anchor: 58.38420048 24.63854749 38.75341415",
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


def test_beta15_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    assert version.startswith("0.4.4-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 15
    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta15.md").read_text(
        encoding="utf-8"
    )
    for phrase in ("read-only", "H2", "i1", "candidate_minus_active", "does **not** change"):
        assert phrase in notes


def test_h2_reports_rtk_and_explicit_reference_relationships_without_mutation() -> None:
    geometry = deepcopy(_H215)
    active = geo.georeference_from_geometry(geometry)
    assert active is not None
    geometry_before = deepcopy(geometry)
    active_before = deepcopy(active)

    diagnostics = reference_candidate_diagnostics(geometry, active)

    assert diagnostics["read_only"] is True
    assert diagnostics["vendor_metadata"]["map_north_offset_present"] is True
    assert diagnostics["vendor_metadata"]["rtk_anchor_present"] is True
    assert diagnostics["vendor_metadata"]["rtk_bias_present"] is False

    relations = diagnostics["absolute_reference_relations"]
    # H2 origin_gps and RTK_anchor are effectively the same absolute reference.
    assert relations["rtk_anchor_minus_origin_gps"]["distance_m"] < 0.2

    candidates = diagnostics["candidate_offsets"]
    assert candidates["vendor_center_gps"]["candidate_minus_active"]["distance_m"] < 0.001
    assert candidates["explicit_vendor_transform_at_origin"]["candidate_minus_active"]["distance_m"] < 0.001

    assert geometry == geometry_before
    assert active == active_before


def test_i1_static_fit_exposes_submetre_vendor_tie_residual_vectors() -> None:
    active = _static_map_georeference(deepcopy(_I108))
    assert active is not None
    diagnostics = reference_candidate_diagnostics(_I108, active)

    assert diagnostics["vendor_metadata"]["map_north_offset_present"] is False
    assert diagnostics["vendor_metadata"]["rtk_anchor_present"] is False
    candidates = diagnostics["candidate_offsets"]
    for name in (
        "vendor_origin_gps_at_local_origin",
        "vendor_center_gps",
        "vendor_south_west_gps",
        "vendor_north_east_gps",
    ):
        assert candidates[name]["candidate_minus_active"]["distance_m"] < 0.25


def test_reference_diagnostics_do_not_export_raw_wgs84_coordinates() -> None:
    active = geo.georeference_from_geometry(_H215)
    assert active is not None
    diagnostics = reference_candidate_diagnostics(_H215, active)
    payload = json.dumps(diagnostics, sort_keys=True)
    for private_coordinate_fragment in (
        "58.38420048",
        "24.63854749",
        "58.384281158447266",
        "24.638553619384766",
    ):
        assert private_coordinate_fragment not in payload


def test_runtime_diagnostics_attach_reference_candidates() -> None:
    source = (COMPONENT / "georeference_diagnostics_semantics.py").read_text(
        encoding="utf-8"
    )
    assert "reference_candidate_diagnostics" in source
    assert 'decorated["reference_candidates"]' in source
