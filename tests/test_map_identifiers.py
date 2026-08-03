"""Regression tests for H1 map identifier fallback."""
from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE = Path(__file__).parents[1] / "custom_components/navimower/map_identifiers.py"
spec = importlib.util.spec_from_file_location("navimower_map_identifiers", MODULE)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def test_h1_zero_location_ids_fall_back_to_map_list() -> None:
    location = {"map_id": "0", "map_base_id": 0, "map_edit_time": 0}
    map_list = [
        {
            "map_id": "1",
            "map_base_id": "5439984",
            "edittime": "1785602279",
        }
    ]
    assert module.resolve_map_identifiers(location, map_list) == (
        "1",
        "5439984",
        "1785602279",
    )


def test_valid_location_ids_remain_preferred() -> None:
    location = {
        "map_id": "7",
        "map_base_id": 123,
        "map_edit_time": 456,
    }
    map_list = [{"map_id": "1", "map_base_id": "999"}]
    assert module.resolve_map_identifiers(location, map_list) == (
        "7",
        "123",
        "456",
    )


def test_nested_map_list_is_supported() -> None:
    assert module.resolve_map_identifiers(
        {},
        {"list": [{"mapId": 3, "mapBaseId": 44, "editTime": 55}]},
    ) == ("3", "44", "55")
