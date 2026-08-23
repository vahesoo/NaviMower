"""Regression guards for 0.4.3-beta30 Custom Area import prototype."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _load_custom_area():
    spec = importlib.util.spec_from_file_location(
        "navimower_custom_area_beta30_test", COMPONENT / "custom_area.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_polygon_diff_ignores_vendor_start_vertex_and_winding() -> None:
    custom = _load_custom_area()
    square = [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]
    rotated = [[2, 0], [2, 2], [0, 2], [0, 0], [2, 0]]
    reversed_square = list(reversed(square))
    candidate = [[4, 4], [6, 4], [6, 5], [4, 5], [4, 4]]

    assert custom.polygon_fingerprint(square) == custom.polygon_fingerprint(rotated)
    assert custom.polygon_fingerprint(square) == custom.polygon_fingerprint(reversed_square)
    assert custom.find_new_polygons([square], [candidate, rotated]) == [candidate[:-1]]


def test_detected_polygon_can_be_persisted_independently() -> None:
    custom = _load_custom_area()
    polygon = [[1, 1], [3, 1], [3, 2], [1, 2]]
    area = custom.create_custom_area("Front door", polygon)
    assert area is not None
    parsed = custom.parse_custom_areas([area.as_dict()])
    assert len(parsed) == 1
    assert parsed[0].name == "Front door"
    assert parsed[0].polygon == ((1.0, 1.0), (3.0, 1.0), (3.0, 2.0), (1.0, 2.0))
    assert parsed[0].source == "navimow_off_limit_import"


def test_options_flow_has_guided_custom_area_capture_without_robot_map_writes() -> None:
    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    assert '"custom_areas"' in source
    assert "async_step_custom_area_add" in source
    assert "async_step_custom_area_detect" in source
    assert "find_new_polygons(" in source
    assert "await self._refresh_map_for_custom_area()" in source
    assert 'for endpoint in ("index2", "location", "map_list")' in source
    assert "OPT_CUSTOM_AREAS: values" in source
    assert "send_setting_device" not in source
    assert "save_setting_iot" not in source
    assert ".mow(" not in source


def test_beta29_production_flow_is_retained_as_base() -> None:
    base = (COMPONENT / "config_flow_base.py").read_text(encoding="utf-8")
    assert "class NavimowConfigFlow(" in base
    assert "class NavimowOptionsFlow(OptionsFlowWithReload)" in base
    assert "async_step_navimower_schedule" in base
    assert "async_step_gates" in base
    assert "async_step_channels" in base


def test_manifest_is_beta30() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta30"
