"""Regression coverage for Custom Area geometry in the map-card payload."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAP_API = ROOT / "custom_components" / "navimower" / "map_api.py"


def test_map_payload_exposes_persistent_custom_areas() -> None:
    source = MAP_API.read_text(encoding="utf-8")
    assert "from .custom_area import OPT_CUSTOM_AREAS, parse_custom_areas" in source
    assert '"custom_areas": [' in source
    assert "area.as_dict()" in source
    assert "coordinator.entry.options.get(OPT_CUSTOM_AREAS)" in source
    assert "await coordinator.async_map_payload()" in source
    assert "coordinator._map_payload_with_sessions(sessions, daily_trails)" in source
    assert "return _with_card_metadata(coordinator, await coordinator.async_map_payload())" in source
    assert "return _with_card_metadata(coordinator, payload)" in source
