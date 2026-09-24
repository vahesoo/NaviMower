"""Regression contracts for Navimower 0.4.5-beta35."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta35_version() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.5-beta35"


def test_beta35_model_profile_marks_only_proven_lidar_family() -> None:
    source = (COMPONENT / "model_capabilities.py").read_text(encoding="utf-8")
    assert "lidar_terrain_overlay: bool = False" in source
    assert "FAMILY_I2_LIDAR" in source
    assert "lidar_terrain_overlay=True" in source


def test_beta35_frontend_terrain_metadata_exposes_support_flag() -> None:
    terrain = (COMPONENT / "terrain_overlay.py").read_text(encoding="utf-8")
    map_api = (COMPONENT / "map_api.py").read_text(encoding="utf-8")

    assert "def supported(self) -> bool:" in terrain
    assert "capability_profile(model, vehicle_type).lidar_terrain_overlay" in terrain
    assert '"supported": self.supported' in terrain
    assert '"supported": self.supported or bool(images)' in terrain
    assert '"supported": False' in map_api


def test_beta35_non_lidar_mowers_do_not_poll_terrain_endpoint() -> None:
    terrain = (COMPONENT / "terrain_overlay.py").read_text(encoding="utf-8")
    assert 'if not self.supported:\n            return' in terrain
    assert 'TERRAIN_ENDPOINT = "/mowerbot/vehicle/common/get-iot-file"' in terrain
