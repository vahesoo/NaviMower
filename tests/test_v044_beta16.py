"""Regression tests for Navimower 0.4.4-beta16 cartographic alignment."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path

from custom_components.navimower import georeference as geo
from custom_components.navimower import georeference_cartographic_semantics as cart
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


def test_beta16_version_release_notes_and_runtime_order() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    assert version.startswith("0.4.4-beta")
    beta = int(version.rsplit("beta", 1)[1])
    assert beta >= 16
    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta16.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "EPSG:8366",
        "translation only",
        "0.93 m",
        "X3",
        "ITRF2014-like dynamic GNSS",
    ):
        assert phrase in notes

    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    x3_index = runtime.index("install_georeference_x3_bias_semantics()")
    diagnostics_index = runtime.index("install_georeference_diagnostics_semantics()")
    assert x3_index < diagnostics_index
    if beta == 16:
        assert "install_georeference_cartographic_semantics()" in runtime
    else:
        # Later betas keep the beta16 module for direct diagnostics/tests but no
        # longer apply the undeclared-CRS assumption in production runtime.
        assert "install_georeference_cartographic_semantics()" not in runtime


def test_epsg8366_estonia_offset_matches_proj_reference_value() -> None:
    correction = cart._itrf2014_to_etrf2014_offset(  # noqa: SLF001
        58.3842,
        24.63855,
        2026.67,
    )
    assert correction is not None
    assert math.isclose(correction["east_m"], -0.7663, abs_tol=0.003)
    assert math.isclose(correction["north_m"], -0.5197, abs_tol=0.003)
    assert math.isclose(correction["distance_m"], 0.9259, abs_tol=0.004)
    assert math.isclose(correction["bearing_deg_from_north"], 235.86, abs_tol=0.2)


def test_h2_explicit_vendor_frame_gets_translation_only_cartographic_shift() -> None:
    vendor = geo.georeference_from_geometry(deepcopy(_H215))
    assert vendor is not None
    active = deepcopy(vendor)
    active.update({"schema_version": 2, "source": "cloud_location_fit", "status": "validated"})
    geometry = {
        "_vendor_georeference": vendor,
        "georeference": deepcopy(active),
        "edit_time": "1788098977",
    }

    before_reference = deepcopy(active["reference"])
    before_rotation = active["rotation_rad"]
    result = cart._apply_cartographic_frame(  # noqa: SLF001
        geometry,
        active,
        None,
        epoch_override=2026.67,
    )
    assert result is not None
    frame = result["cartographic_frame"]
    assert frame["applied"] is True
    assert frame["support_kind"] == "explicit_vendor_map_north_offset"
    assert frame["translation_only"] is True
    assert result["source"] == "cloud_location_fit"
    assert result["rotation_rad"] == before_rotation
    assert result["reference"]["local_x"] == before_reference["local_x"]
    assert result["reference"]["local_y"] == before_reference["local_y"]

    displacement = geo.wgs84_offset_m(
        before_reference["latitude"],
        before_reference["longitude"],
        result["reference"]["latitude"],
        result["reference"]["longitude"],
    )
    assert displacement is not None
    # When beta17+ installs ellipsoidal geodesy these values describe the same
    # physical EPSG displacement instead of the old spherical approximation.
    assert math.isclose(displacement[0], -0.766, abs_tol=0.02)
    assert math.isclose(displacement[1], -0.520, abs_tol=0.02)


def test_i1_static_vendor_fit_gets_same_cartographic_frame_correction() -> None:
    vendor = _static_map_georeference(deepcopy(_I108))
    assert vendor is not None
    geometry = {
        "_vendor_georeference": deepcopy(vendor),
        "georeference": deepcopy(vendor),
        "edit_time": "1781273946",
    }
    result = cart._apply_cartographic_frame(  # noqa: SLF001
        geometry,
        vendor,
        None,
        epoch_override=2026.67,
    )
    assert result is not None
    frame = result["cartographic_frame"]
    assert frame["applied"] is True
    assert frame["support_kind"] == "static_vendor_ties"
    assert result["source"] == "vendor_map_static_fit"
    assert math.isclose(frame["distance_m"], 0.926, abs_tol=0.01)


def test_x3_vendor_rtk_translation_is_never_stacked_with_etrs89_shift() -> None:
    active = {
        "schema_version": 2,
        "source": "x3_rtk_anchor",
        "status": "validated",
        "reference": {
            "local_x": 0.0,
            "local_y": 0.0,
            "latitude": 59.1806,
            "longitude": 26.8622,
        },
        "rotation_rad": 0.0,
        "rtk_validation": {
            "bias_correction": {"applied": True},
        },
    }
    geometry = {"_vendor_georeference": deepcopy(active), "georeference": deepcopy(active)}
    result = cart._apply_cartographic_frame(  # noqa: SLF001
        geometry,
        active,
        None,
        epoch_override=2026.67,
    )
    assert result is not None
    assert result["reference"] == active["reference"]
    assert result["cartographic_frame"]["applied"] is False
    assert result["cartographic_frame"]["reason"] == "x3_vendor_rtk_frame_owns_translation"


def test_epsg8366_correction_is_not_applied_outside_europe() -> None:
    vendor = {
        "schema_version": 1,
        "source": "vendor_map_detail",
        "status": "validated",
        "reference": {
            "local_x": 0.0,
            "local_y": 0.0,
            "latitude": 37.7749,
            "longitude": -122.4194,
        },
        "rotation_rad": 0.0,
    }
    active = deepcopy(vendor)
    active["source"] = "cloud_location_fit"
    geometry = {"_vendor_georeference": vendor}
    result = cart._apply_cartographic_frame(  # noqa: SLF001
        geometry,
        active,
        None,
        epoch_override=2026.67,
    )
    assert result is not None
    assert result["reference"] == active["reference"]
    assert result["cartographic_frame"]["applied"] is False
    assert result["cartographic_frame"]["reason"] == "outside_epsg8366_area"


def test_reference_diagnostics_use_cached_vendor_evidence_after_geometry_reduction() -> None:
    vendor = geo.georeference_from_geometry(deepcopy(_H215))
    assert vendor is not None
    vendor = deepcopy(vendor)
    vendor["vendor_metadata"] = {
        "map_north_offset_present": True,
        "origin_gps_present": True,
        "center_gps_present": True,
        "south_west_gps_present": False,
        "north_east_gps_present": False,
        "rtk_anchor_present": True,
        "rtk_bias_present": False,
        "rtk_pile_present": False,
        "map_circle_center_local": list(_H215["map_circle_center"]),
        "map_width_m": 90.0,
        "map_height_m": 80.0,
    }
    vendor["rtk_anchor"] = {
        "latitude": 58.38420048,
        "longitude": 24.63854749,
        "altitude_m": 38.75341415,
    }
    geometry = {
        "_vendor_georeference": vendor,
        "station": {"x": -0.3309, "y": -2.7012},
    }
    diagnostics = reference_candidate_diagnostics(geometry, vendor)
    metadata = diagnostics["vendor_metadata"]
    assert metadata["map_north_offset_present"] is True
    assert metadata["origin_gps_present"] is True
    assert metadata["center_gps_present"] is True
    assert metadata["rtk_anchor_present"] is True
    relation = diagnostics["absolute_reference_relations"]["rtk_anchor_minus_origin_gps"]
    assert relation["distance_m"] < 0.2
