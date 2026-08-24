"""Regression guards retained from beta33."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_custom_area_occupancy_requires_fresh_mqtt_xy() -> None:
    source = (COMPONENT / "binary_sensor.py").read_text(encoding="utf-8")
    assert "position = self.coordinator._fresh_mqtt_position()" in source
    assert "point_in_polygon(position[\"x\"], position[\"y\"], self.area.polygon)" in source
    assert "return super().available and self.coordinator._fresh_mqtt_position() is not None" in source


def test_manifest_keeps_beta33_or_later_043_beta() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] in {"0.4.3-beta33", "0.4.3-beta34", "0.4.3-beta35", "0.4.3-beta36"}
