"""Release contract for Navimower 0.4.4-beta23 provider georeference frames."""
from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

from custom_components.navimower.georeference import offset_wgs84
from custom_components.navimower.georeference_frames import (
    FRAME_ACTIVE,
    FRAME_REGIONAL_CARTOGRAPHIC,
    FRAME_WEB_WGS84,
    build_georeference_frames,
    georeference_frame_diagnostics,
    site_underlay_origins,
)

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "navimower"


def _geo(
    *,
    latitude: float = 59.0,
    longitude: float = 24.0,
    rotation_rad: float = 0.1,
    source: str = "vendor_map_static_fit",
    cartographic_frame: dict | None = None,
) -> dict:
    value = {
        "source": source,
        "status": "validated",
        "geodesy_model": "wgs84_ellipsoid_v1",
        "reference": {
            "local_x": 0.0,
            "local_y": 0.0,
            "latitude": latitude,
            "longitude": longitude,
        },
        "rotation_rad": rotation_rad,
        "validation": {"valid": True, "status": "validated"},
    }
    if cartographic_frame is not None:
        value["cartographic_frame"] = cartographic_frame
    return value


def _coordinator(active: dict, *, fit: dict | None = None):
    calibration = {
        "geodesy_model": "wgs84_ellipsoid_v1",
        "fit": fit,
    }
    return SimpleNamespace(
        data={"georeference": active},
        _map_geometry={"_georeference_calibration": calibration},
    )


def test_manifest_release_notes_and_runtime_order() -> None:
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    assert manifest["version"] == "0.4.4-beta23"
    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta23.md").read_text()
    for token in (
        "web_wgs84",
        "regional_cartographic",
        "OpenStreetMap",
        "Google Satellite",
        "15 m baseline",
        "Multi mower",
        "Exact frame latitude/longitude values are not added to diagnostics",
    ):
        assert token.lower() in notes.lower()

    runtime = (INTEGRATION / "runtime.py").read_text()
    assert "install_georeference_frames_semantics" in runtime
    assert runtime.index("install_georeference_cartographic_semantics()") < runtime.index(
        "install_georeference_frames_semantics()"
    )


def test_cartographic_active_exports_inverse_web_frame() -> None:
    active = _geo(
        cartographic_frame={
            "applied": True,
            "east_m": -0.762,
            "north_m": -0.517,
            "translation_only": True,
        }
    )
    frames = build_georeference_frames(_coordinator(active))
    assert frames[FRAME_ACTIVE]["available"] is True
    assert frames[FRAME_REGIONAL_CARTOGRAPHIC]["available"] is True
    assert frames[FRAME_REGIONAL_CARTOGRAPHIC]["source"] == "active_cartographic"

    web = frames[FRAME_WEB_WGS84]
    assert web["available"] is True
    assert web["source"] == "inverse_active_cartographic_translation"
    assert web["offset_from_active"]["east_m"] == 0.762
    assert web["offset_from_active"]["north_m"] == 0.517
    assert web["georeference"]["rotation_rad"] == active["rotation_rad"]
    assert web["georeference"]["reference"]["local_x"] == 0.0
    assert web["georeference"]["reference"]["local_y"] == 0.0


def test_vendor_rtk_regional_frame_uses_mature_cloud_translation_for_web() -> None:
    active = _geo(
        source="x3_rtk_anchor",
        rotation_rad=0.2,
        cartographic_frame={
            "applied": False,
            "reason": "x3_vendor_rtk_frame_owns_translation",
        },
    )
    target = offset_wgs84(59.0, 24.0, 0.85, 0.01)
    assert target is not None
    learned = _geo(
        latitude=target[0],
        longitude=target[1],
        rotation_rad=0.202,
        source="cloud_location_fit",
    )
    learned["calibration"] = {
        "sample_count": 24,
        "inlier_count": 24,
        "baseline_m": 60.75,
        "rms_error_m": 0.21,
        "max_error_m": 0.419,
        "observed_scale": 1.000739,
    }

    frames = build_georeference_frames(_coordinator(active, fit=learned))
    regional = frames[FRAME_REGIONAL_CARTOGRAPHIC]
    assert regional["available"] is True
    assert regional["source"] == "vendor_rtk_regional"
    assert regional["offset_from_active"]["distance_m"] == 0.0

    web = frames[FRAME_WEB_WGS84]
    assert web["available"] is True
    assert web["source"] == "cloud_translation_fit"
    assert math.isclose(web["offset_from_active"]["east_m"], 0.85, abs_tol=0.02)
    assert math.isclose(web["offset_from_active"]["north_m"], 0.01, abs_tol=0.02)
    assert web["georeference"]["rotation_rad"] == active["rotation_rad"]
    assert web["quality"]["sample_count"] == 24


def test_vendor_rtk_web_frame_waits_for_mature_evidence() -> None:
    active = _geo(
        source="x3_rtk_anchor",
        cartographic_frame={
            "applied": False,
            "reason": "x3_vendor_rtk_frame_owns_translation",
        },
    )
    learned = _geo(source="cloud_location_fit", rotation_rad=0.1)
    learned["calibration"] = {
        "sample_count": 4,
        "inlier_count": 4,
        "baseline_m": 8.0,
        "rms_error_m": 0.2,
        "max_error_m": 0.4,
        "observed_scale": 1.0,
    }
    web = build_georeference_frames(_coordinator(active, fit=learned))[FRAME_WEB_WGS84]
    assert web["available"] is False
    assert web["reason"] == "insufficient_samples"


def test_nonregional_dynamic_active_is_valid_web_fallback() -> None:
    active = _geo(latitude=40.0, longitude=-74.0, source="cloud_location_fit")
    frames = build_georeference_frames(_coordinator(active))
    assert frames[FRAME_WEB_WGS84]["available"] is True
    assert frames[FRAME_WEB_WGS84]["source"] == "active"
    assert frames[FRAME_REGIONAL_CARTOGRAPHIC]["available"] is False
    assert frames[FRAME_REGIONAL_CARTOGRAPHIC]["reason"] == "no_regional_cartographic_provider"


def test_multi_site_origins_follow_frame_translation_only() -> None:
    active = _geo(
        cartographic_frame={
            "applied": True,
            "east_m": -0.762,
            "north_m": -0.517,
        }
    )
    coordinator = _coordinator(active)
    origins = site_underlay_origins({"latitude": 59.0, "longitude": 24.0}, coordinator)
    assert origins[FRAME_ACTIVE]["available"] is True
    assert origins[FRAME_REGIONAL_CARTOGRAPHIC]["available"] is True
    assert origins[FRAME_WEB_WGS84]["available"] is True
    expected = offset_wgs84(59.0, 24.0, 0.762, 0.517)
    assert expected is not None
    assert math.isclose(origins[FRAME_WEB_WGS84]["latitude"], expected[0], abs_tol=1e-10)
    assert math.isclose(origins[FRAME_WEB_WGS84]["longitude"], expected[1], abs_tol=1e-10)


def test_diagnostics_and_api_semantics_are_privacy_safe() -> None:
    active = _geo(
        cartographic_frame={
            "applied": True,
            "east_m": -0.762,
            "north_m": -0.517,
        }
    )
    diagnostics = georeference_frame_diagnostics(_coordinator(active))
    text = repr(diagnostics).lower()
    assert "latitude" not in text
    assert "longitude" not in text
    assert diagnostics[FRAME_WEB_WGS84]["offset_from_active"]["distance_m"] > 0.9

    semantics = (INTEGRATION / "georeference_frames_semantics.py").read_text()
    for token in (
        '"openstreetmap": FRAME_WEB_WGS84',
        '"google_satellite": FRAME_WEB_WGS84',
        '"estonia_orthophoto": FRAME_REGIONAL_CARTOGRAPHIC',
        '"estonia_hybrid": FRAME_REGIONAL_CARTOGRAPHIC',
        'result["georeference_frames"] = frames',
        'result["underlay_origins"] = site_underlay_origins',
        'provider_metadata["reference_frame"] = frame_name',
        'provider_metadata["reference_frame_fallback"] = FRAME_ACTIVE',
    ):
        assert token in semantics
