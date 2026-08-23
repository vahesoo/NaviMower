"""Regression guards for 0.4.3-beta33 Custom Area occupancy."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _load_custom_area():
    spec = importlib.util.spec_from_file_location(
        "navimower_custom_area_beta33_test", COMPONENT / "custom_area.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_point_in_polygon_includes_inside_and_boundary_but_not_outside() -> None:
    custom = _load_custom_area()
    square = [[0, 0], [4, 0], [4, 4], [0, 4]]
    assert custom.point_in_polygon(2, 2, square) is True
    assert custom.point_in_polygon(0, 2, square) is True
    assert custom.point_in_polygon(4, 4, square) is True
    assert custom.point_in_polygon(5, 2, square) is False


def test_binary_sensor_creates_one_entity_per_saved_custom_area() -> None:
    source = (COMPONENT / "binary_sensor.py").read_text(encoding="utf-8")
    assert "parse_custom_areas(entry.options.get(OPT_CUSTOM_AREAS))" in source
    assert "NavimowerCustomAreaBinarySensor" in source
    assert 'super().__init__(coordinator, f"custom_area_{area.slug}")' in source
    assert 'self._attr_name = area.name' in source


def test_custom_area_occupancy_requires_fresh_mqtt_xy() -> None:
    source = (COMPONENT / "binary_sensor.py").read_text(encoding="utf-8")
    assert "position = self.coordinator._fresh_mqtt_position()" in source
    assert "point_in_polygon(position[\"x\"], position[\"y\"], self.area.polygon)" in source
    assert "return super().available and self.coordinator._fresh_mqtt_position() is not None" in source


def test_manifest_keeps_beta33_or_later_043_beta() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] in {"0.4.3-beta33", "0.4.3-beta34"}
