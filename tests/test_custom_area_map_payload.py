"""Regression coverage for Custom Area geometry in the map-card payload."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = ROOT / "custom_components" / "navimower" / "coordinator.py"


def test_map_payload_exposes_persistent_custom_areas() -> None:
    source = COORDINATOR.read_text(encoding="utf-8")
    assert "from .custom_area import OPT_CUSTOM_AREAS, parse_custom_areas" in source
    assert '"custom_areas": [' in source
    assert "area.as_dict()" in source
    assert "parse_custom_areas(self.entry.options.get(OPT_CUSTOM_AREAS))" in source
