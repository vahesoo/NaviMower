"""Regression tests for Navimower 0.4.4-beta20 translation refinement."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path

from custom_components.navimower import georeference as geo
from custom_components.navimower import georeference_cartographic_semantics as cart
from custom_components.navimower import georeference_diagnostics_frame_semantics as diag_frame
from custom_components.navimower import georeference_geodesy_semantics as geodesy
from custom_components.navimower import georeference_translation_refinement_semantics as refine

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _static_transform() -> dict:
    return {
        "schema_version": 2,
        "source": "vendor_map_static_fit",
        "status": "validated",
        "geodesy_model": "wgs84_ellipsoid_v1",
        "anchor_policy": "static_map_primary",
        "reference": {
            "local_x": 0.0,
            "local_y": 0.0,
            "latitude": 58.3842,
            "longitude": 24.63855,
        },
        "rotation_rad": 0.31,
        "static_validation": {"valid": True},
    }


def _learned_transform(east_m: float = 1.457, north_m: float = -0.940) -> dict:
    target = geodesy.offset_wgs84_ellipsoid(58.3842, 24.63855, east_m, north_m)
    assert target is not None
    return {
        "schema_version": 2,
        "source": "cloud_location_fit",
        "status": "validated",
        "geodesy_model": "wgs84_ellipsoid_v1",
        "reference": {
            "local_x": 0.0,
            "local_y": 0.0,
            "latitude": target[0],
            "longitude": target[1],
        },
        "rotation_rad": 0.31 + math.radians(0.5),
        "calibration": {
            "sample_count": 16,
            "inlier_count": 16,
            "baseline_m": 46.6,
            "rms_error_m": 0.18,
            "max_error_m": 0.42,
            "observed_scale": 0.9991,
        },
    }


def _geometry(static: dict, learned: dict) -> dict:
    return {
        "revision": "map-i1",
        "edit_time": "1781273946",
        "_vendor_georeference": deepcopy(static),
        "_georeference_calibration": {
            "map_revision": "map-i1",
            "fit": deepcopy(learned),
            "geodesy_model": "wgs84_ellipsoid_v1",
            "samples": [],
        },
        "georeference": deepcopy(static),
    }


def test_beta20_release_contract_and_runtime_order() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.4-beta20"

    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta20.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "translation only",
        "1.7 m",
        "clock 4",
        "X3",
        "ETRS89",
        "candidate_minus_active",
    ):
        assert phrase in notes

    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    x3 = runtime.index("install_georeference_x3_bias_semantics()")
    refinement = runtime.index("install_georeference_translation_refinement_semantics()")
    state = runtime.index("install_georeference_geodesy_state_semantics()")
    cartographic = runtime.index("install_georeference_cartographic_semantics()")
    frame_diag = runtime.index("install_georeference_diagnostics_frame_semantics()")
    diagnostics = runtime.index("install_georeference_diagnostics_semantics()")
    assert x3 < refinement < state < cartographic < frame_diag < diagnostics


def test_static_geometry_keeps_rotation_and_uses_learned_translation(monkeypatch) -> None:
    monkeypatch.setattr(geo, "offset_wgs84", geodesy.offset_wgs84_ellipsoid)
    monkeypatch.setattr(geo, "wgs84_offset_m", geodesy.wgs84_offset_m_ellipsoid)

    static = _static_transform()
    learned = _learned_transform()
    geometry = _geometry(static, learned)

    result = refine._refine_static_translation(geometry, static, None)  # noqa: SLF001

    assert result["source"] == "vendor_map_static_fit"
    assert result["rotation_rad"] == static["rotation_rad"]
    assert result["anchor_policy"] == "static_map_geometry_cloud_translation_refined"
    metadata = result["translation_refinement"]
    assert metadata["applied"] is True
    assert math.isclose(metadata["east_m"], 1.457, abs_tol=0.003)
    assert math.isclose(metadata["north_m"], -0.940, abs_tol=0.003)
    assert math.isclose(metadata["distance_m"], 1.734, abs_tol=0.004)
    assert math.isclose(metadata["bearing_deg_from_north"], 122.8, abs_tol=0.3)

    projected = geo.local_xy_to_wgs84(result, 0.0, 0.0)
    learned_projected = geo.local_xy_to_wgs84(learned, 0.0, 0.0)
    assert projected is not None and learned_projected is not None
    residual = geodesy.wgs84_offset_m_ellipsoid(
        projected[0], projected[1], learned_projected[0], learned_projected[1]
    )
    assert residual is not None
    assert math.hypot(*residual) < 0.01


def test_etrs89_stacks_after_cloud_translation_refinement(monkeypatch) -> None:
    monkeypatch.setattr(geo, "offset_wgs84", geodesy.offset_wgs84_ellipsoid)
    monkeypatch.setattr(geo, "wgs84_offset_m", geodesy.wgs84_offset_m_ellipsoid)

    static = _static_transform()
    learned = _learned_transform()
    geometry = _geometry(static, learned)
    refined = refine._refine_static_translation(geometry, static, None)  # noqa: SLF001
    before = deepcopy(refined)

    result = cart._apply_cartographic_frame(  # noqa: SLF001
        geometry,
        refined,
        None,
        epoch_override=2026.67,
    )
    assert result is not None
    assert result["translation_refinement"]["applied"] is True
    assert result["rotation_rad"] == static["rotation_rad"]
    assert result["cartographic_frame"]["applied"] is True

    displacement = geodesy.wgs84_offset_m_ellipsoid(
        before["reference"]["latitude"],
        before["reference"]["longitude"],
        result["reference"]["latitude"],
        result["reference"]["longitude"],
    )
    assert displacement is not None
    assert math.isclose(displacement[0], -0.766, abs_tol=0.01)
    assert math.isclose(displacement[1], -0.520, abs_tol=0.01)


def test_x3_and_explicit_h2_paths_are_not_translation_refined() -> None:
    learned = _learned_transform()
    geometry = _geometry(_static_transform(), learned)

    x3 = _static_transform()
    x3["source"] = "x3_rtk_anchor"
    x3["anchor_policy"] = "x3_rtk_anchor_bias_primary"
    assert refine._refine_static_translation(geometry, x3, None) == x3  # noqa: SLF001

    h2 = _static_transform()
    h2["source"] = "vendor_map_detail"
    h2["anchor_policy"] = "explicit_vendor_map_north_offset"
    assert refine._refine_static_translation(geometry, h2, None) == h2  # noqa: SLF001


def test_translation_refinement_requires_mature_fit() -> None:
    static = _static_transform()
    learned = _learned_transform()
    learned["calibration"]["sample_count"] = 5
    learned["calibration"]["inlier_count"] = 5
    geometry = _geometry(static, learned)

    result = refine._refine_static_translation(geometry, static, None)  # noqa: SLF001
    metadata = result["translation_refinement"]
    assert metadata["applied"] is False
    assert metadata["reason"] == "insufficient_samples"
    assert result["reference"] == static["reference"]


def test_candidate_diagnostics_remove_common_cartographic_translation(monkeypatch) -> None:
    raw = {
        "read_only": True,
        "available": True,
        "convention": "candidate_minus_active",
        "cartographic_frame": {
            "applied": True,
            "east_m": -0.762,
            "north_m": -0.517,
        },
        "candidate_offsets": {
            "private_cloud_live_gps": {
                "meaning": "current private-cloud GPS",
                "candidate_minus_active": {
                    "east_m": 2.219,
                    "north_m": -0.423,
                    "distance_m": 2.259,
                    "bearing_deg_from_north": 100.8,
                },
            }
        },
    }

    monkeypatch.setattr(
        diag_frame,
        "_ORIGINAL_REFERENCE_DIAGNOSTICS",
        lambda *args, **kwargs: deepcopy(raw),
    )
    result = diag_frame._normalized_reference_candidate_diagnostics(  # noqa: SLF001
        {}, {}, None
    )
    delta = result["candidate_offsets"]["private_cloud_live_gps"][
        "candidate_minus_active"
    ]
    assert math.isclose(delta["east_m"], 1.457, abs_tol=0.001)
    assert math.isclose(delta["north_m"], -0.940, abs_tol=0.001)
    assert math.isclose(delta["distance_m"], 1.734, abs_tol=0.004)
    assert math.isclose(delta["bearing_deg_from_north"], 122.8, abs_tol=0.3)
    normalization = result["candidate_cartographic_normalization"]
    assert normalization["applied"] is True
    assert normalization["candidate_count"] == 1
