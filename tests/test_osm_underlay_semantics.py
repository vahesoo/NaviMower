"""Regression checks for the authenticated OpenStreetMap tile proxy."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "custom_components" / "navimower" / "osm_underlay_semantics.py"


def _load_target():
    spec = importlib.util.spec_from_file_location("osm_underlay_semantics_test", TARGET)
    target = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(target)
    return target


def test_osm_proxy_policy_and_coordinates() -> None:
    target = _load_target()
    assert target._OSM_TILE_URL == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    assert target._OSM_CACHE_TTL_SECONDS >= 7 * 24 * 60 * 60
    assert "Navimower" in target._OSM_USER_AGENT
    assert "github.com/vahesoo/NaviMower" in target._OSM_USER_AGENT

    class Request:
        def __init__(self, query):
            self.query = query

    assert target._tile_coordinates(Request({})) is None
    assert target._tile_coordinates(
        Request({"osm_tile": "1", "z": "18", "x": "147000", "y": "76000"})
    ) == (18, 147000, 76000)
    assert target._tile_coordinates(
        Request({"osm_tile": "1", "z": "20", "x": "0", "y": "0"})
    ) == (-1, -1, -1)


def test_osm_proxy_is_cached_authenticated_map_mode() -> None:
    source = TARGET.read_text(encoding="utf-8")
    assert 'request.query.get("osm_tile")' in source
    assert 'NavimowerMapView.get = get_with_osm' in source
    assert '"User-Agent": _OSM_USER_AGENT' in source
    assert '"Referer": _OSM_REFERER' in source
    assert "async_add_executor_job(_cache_state" in source
    assert "async_add_executor_job(_write_tile" in source
    assert '"Cache-Control": f"private, max-age={_OSM_CACHE_TTL_SECONDS}"' in source
