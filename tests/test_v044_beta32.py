"""Regression coverage for Navimower 0.4.4-beta32 polygon gate areas."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
CHANNEL = COMPONENT / "channel.py"


def _load_channel_module():
    spec = importlib.util.spec_from_file_location("navimower_channel_beta32", CHANNEL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_polygon_gate_area_uses_exact_geometry_and_keeps_compatibility_bounds() -> None:
    channel = _load_channel_module()
    polygon = [
        [-28.75, 8.00],
        [-23.50, 5.25],
        [-19.25, 10.75],
        [-24.00, 14.75],
    ]
    parsed = channel.parse_channels(
        [
            {
                "name": "ProChan",
                "x_min": 0,
                "x_max": 1,
                "y_min": 0,
                "y_max": 1,
                "polygon": json.dumps(polygon),
            }
        ]
    )
    assert len(parsed) == 1
    area = parsed[0]
    assert area.polygon is not None
    assert (area.x_min, area.x_max, area.y_min, area.y_max) == (
        -28.75,
        -19.25,
        5.25,
        14.75,
    )

    # A point near the polygon centre is inside.
    assert area.contains(-23.875, 9.6875) is True
    # This point is inside the old min/max bounding box but outside the rotated polygon.
    assert area.contains(-28.5, 14.0) is False
    # Boundary points are intentionally treated as inside for gate safety.
    assert area.contains(-28.75, 8.0) is True

    exported = area.as_dict()
    assert exported["polygon"] == polygon
    assert exported["x_min"] == -28.75
    assert exported["x_max"] == -19.25


def test_legacy_rectangular_gate_areas_remain_unchanged() -> None:
    channel = _load_channel_module()
    parsed = channel.parse_channels(
        [{"name": "Legacy", "x_min": 3, "x_max": 1, "y_min": 4, "y_max": 2}]
    )
    assert len(parsed) == 1
    area = parsed[0]
    assert area.polygon is None
    assert (area.x_min, area.x_max, area.y_min, area.y_max) == (1, 3, 2, 4)
    assert area.contains(2, 3) is True
    assert area.contains(4, 3) is False


def test_invalid_polygon_is_rejected_instead_of_falling_back_to_rectangle() -> None:
    channel = _load_channel_module()
    parsed = channel.parse_channels(
        [
            {
                "name": "Invalid polygon",
                "x_min": 0,
                "x_max": 100,
                "y_min": 0,
                "y_max": 100,
                "polygon": "[[1,2],[3,4]]",
            }
        ]
    )
    assert parsed == []


def test_polygon_options_semantics_are_installed_in_runtime() -> None:
    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    semantics = (COMPONENT / "gate_area_polygon_semantics.py").read_text(encoding="utf-8")
    assert "from .gate_area_polygon_semantics import install_gate_area_polygon_semantics" in runtime
    assert "install_gate_area_polygon_semantics()" in runtime
    assert "NavimowOptionsFlow._channel_schema = staticmethod(_channel_schema)" in semantics
    assert 'vol.Optional("polygon", default=default)' in semantics
    assert "parse_channels" in semantics


def test_beta32_release_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    match = re.fullmatch(r"0\.4\.4-beta(\d+)", str(manifest["version"]))
    assert match is not None and int(match.group(1)) >= 32
    notes = ROOT / ".github" / "release-notes" / "0.4.4-beta32.md"
    assert notes.is_file()
    assert notes.read_text(encoding="utf-8").startswith(
        "title: Navimower 0.4.4-beta32\n"
    )
