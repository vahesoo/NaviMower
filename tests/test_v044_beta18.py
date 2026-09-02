"""Regression tests for Navimower 0.4.4-beta18 geodesy state migration."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from custom_components.navimower import georeference_geodesy_semantics as geodesy

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta18_version_release_notes_and_runtime_order() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    assert version.startswith("0.4.4-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 18

    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta18.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "persisted",
        "wgs84_ellipsoid_v1",
        "fresh map-detail",
        "raw",
        "beta17",
    ):
        assert phrase in notes

    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    helper = runtime.index("install_georeference_geodesy_semantics()")
    georef = runtime.index("install_georeference_semantics()")
    x3 = runtime.index("install_georeference_x3_bias_semantics()")
    state = runtime.index("install_georeference_geodesy_state_semantics()")
    diagnostics = runtime.index("install_georeference_diagnostics_semantics()")
    assert helper < georef < x3 < state < diagnostics


def test_legacy_persisted_georeference_is_invalidated_once(monkeypatch) -> None:
    async def original_load(self) -> None:
        return None

    monkeypatch.setattr(geodesy, "_ORIGINAL_LOAD", original_load)
    coordinator = SimpleNamespace(
        _map_geometry={
            "revision": "map-a",
            "zones": [{"id": 1}],
            "georeference": {
                "source": "cloud_location_fit",
                "status": "validated",
            },
            "_georeference_calibration": {
                "map_revision": "map-a",
                "fit": {"source": "cloud_location_fit"},
            },
            "_vendor_georeference": {"source": "vendor_map_static_fit"},
            "x3_rtk_bias_v1": True,
            "etrs89_cartographic_v1": True,
        },
        _map_cache_key="cached-map-a",
    )

    asyncio.run(geodesy._load_persistent_state(coordinator))  # noqa: SLF001

    geometry = coordinator._map_geometry
    assert geometry["zones"] == [{"id": 1}]
    assert "georeference" not in geometry
    assert "_georeference_calibration" not in geometry
    assert "_vendor_georeference" not in geometry
    assert "x3_rtk_bias_v1" not in geometry
    assert "etrs89_cartographic_v1" not in geometry
    assert coordinator._map_cache_key is None


def test_current_ellipsoid_georeference_is_not_invalidated(monkeypatch) -> None:
    async def original_load(self) -> None:
        return None

    monkeypatch.setattr(geodesy, "_ORIGINAL_LOAD", original_load)
    active = {
        "source": "vendor_map_static_fit",
        "status": "validated",
        "geodesy_model": "wgs84_ellipsoid_v1",
    }
    coordinator = SimpleNamespace(
        _map_geometry={"revision": "map-b", "georeference": active.copy()},
        _map_cache_key="cached-map-b",
    )

    asyncio.run(geodesy._load_persistent_state(coordinator))  # noqa: SLF001

    assert coordinator._map_geometry["georeference"] == active
    assert coordinator._map_cache_key == "cached-map-b"


def test_final_update_marks_active_and_calibration_with_geodesy_model(monkeypatch) -> None:
    def original_update(geometry, location):
        geometry["_georeference_calibration"] = {
            "map_revision": "map-c",
            "fit": {"status": "validated"},
        }
        return {
            "source": "cloud_location_fit",
            "status": "validated",
            "reference": {
                "local_x": 0.0,
                "local_y": 0.0,
                "latitude": 1.0,
                "longitude": 2.0,
            },
            "rotation_rad": 0.0,
        }

    monkeypatch.setattr(geodesy, "_ORIGINAL_UPDATE", original_update)
    geometry = {"revision": "map-c"}
    result = geodesy._update_with_geodesy_model(geometry, None)  # noqa: SLF001

    assert result is not None
    assert result["geodesy_model"] == "wgs84_ellipsoid_v1"
    assert geometry["georeference"]["geodesy_model"] == "wgs84_ellipsoid_v1"
    assert geometry["_georeference_calibration"]["geodesy_model"] == "wgs84_ellipsoid_v1"
