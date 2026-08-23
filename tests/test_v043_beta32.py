"""Regression guards for 0.4.3-beta32 Custom Area capture UX."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_add_custom_area_captures_baseline_immediately() -> None:
    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    start = source.index("    async def async_step_custom_area_add(")
    end = source.index("    async def async_step_custom_area_detect(", start)
    block = source[start:end]
    assert "await self._refresh_map_for_custom_area()" in block
    assert "self._custom_area_baseline = self._off_limit_polygons()" in block
    assert "return await self.async_step_custom_area_detect()" in block
    assert "if user_input is not None:" not in block


def test_beta32_release_notes_remain_available() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta32.md").read_text(encoding="utf-8")
    assert "0.4.3-beta32" in notes
