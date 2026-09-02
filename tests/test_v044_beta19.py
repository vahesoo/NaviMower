"""Regression tests for Navimower 0.4.4-beta19 cartographic restoration."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path

from custom_components.navimower import georeference as geo
from custom_components.navimower import georeference_cartographic_semantics as cart
from custom_components.navimower import georeference_geodesy_semantics as geodesy

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta19_version_release_notes_and_runtime_order() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.4-beta19"

    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta19.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "EPSG:8366",
        "WGS84 ellipsoid",
        "ETRS89",
        "south-west",
        "X3",
        "i1/i108",
    ):
        assert phrase in notes

    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    helper = runtime.index("install_georeference_geodesy_semantics()")
    georef = runtime.index("install_georeference_semantics()")
    x3 = runtime.index("install_georeference_x3_bias_semantics()")
    state = runtime.index("install_georeference_geodesy_state_semantics()")
    cartographic = runtime.index("install_georeference_cartographic_semantics()")
    diagnostics = runtime.index("install_georeference_diagnostics_semantics()")
    assert helper < georef < x3 < state < cartographic < diagnostics


def test_cartographic_shift_runs_on_top_of_ellipsoid_reference(monkeypatch) -> None:
    monkeypatch.setattr(geo, "offset_wgs84", geodesy.offset_wgs84_ellipsoid)

    active = {
        "schema_version": 2,
        "source": "vendor_map_static_fit",
        "status": "validated",
        "geodesy_model": "wgs84_ellipsoid_v1",
        "reference": {
            "local_x": 0.0,
            "local_y": 0.0,
            "latitude": 58.3842,
            "longitude": 24.63855,
        },
        "rotation_rad": 0.25,
    }
    geometry = {
        "georeference": deepcopy(active),
        "_vendor_georeference": deepcopy(active),
        "edit_time": "1788098977",
    }
    before = deepcopy(active)

    result = cart._apply_cartographic_frame(  # noqa: SLF001
        geometry,
        active,
        None,
        epoch_override=2026.67,
    )

    assert result is not None
    frame = result["cartographic_frame"]
    assert frame["applied"] is True
    assert frame["translation_only"] is True
    assert frame["support_kind"] == "static_vendor_ties"
    assert result["geodesy_model"] == "wgs84_ellipsoid_v1"
    assert result["rotation_rad"] == before["rotation_rad"]
    assert result["reference"]["local_x"] == before["reference"]["local_x"]
    assert result["reference"]["local_y"] == before["reference"]["local_y"]

    displacement = geodesy.wgs84_offset_m_ellipsoid(
        before["reference"]["latitude"],
        before["reference"]["longitude"],
        result["reference"]["latitude"],
        result["reference"]["longitude"],
    )
    assert displacement is not None
    assert math.isclose(displacement[0], -0.7663, abs_tol=0.004)
    assert math.isclose(displacement[1], -0.5197, abs_tol=0.004)
