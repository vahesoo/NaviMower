"""Regression contracts for Navimower 0.4.5-beta33."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta33_version_floor() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    assert version.startswith("0.4.5-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 33


def test_beta33_snapshot_source_carries_model_metadata() -> None:
    manager = (COMPONENT / "map_snapshot.py").read_text(encoding="utf-8")
    assert "from .model_capabilities import model_family" in manager
    assert '"model": str(data.get("model") or "")' in manager
    assert '"vehicle_type": data.get("vehicle_type")' in manager
    assert '"model_family": str(' in manager


def test_beta33_renderer_has_model_specific_artwork() -> None:
    render = (COMPONENT / "map_snapshot_render.py").read_text(encoding="utf-8")
    assert "MOWER_RASTER_ART" in render
    assert '"i_light":' in render
    assert '"i2_lidar":' in render
    assert '"x3":' in render
    assert '"x4":' in render
    assert "def _mower_art_key(" in render
    assert "def _raster_mower_art(" in render
    assert "def _paste_raster_mower(" in render
    assert 'family in {"i1", "i2_awd", "i2_unknown"}' in render
    assert 'family == "i2_lidar"' in render
    assert 'family == "x3"' in render
    assert 'family == "x4"' in render
    assert 'family in {"h1", "h2", "h5"}' in render
