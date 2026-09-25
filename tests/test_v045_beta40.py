"""Regression contracts for Navimower 0.4.5-beta40 snapshot fonts."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta40_release_metadata_and_font_dependencies() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    assert version.startswith("0.4.5-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 40
    requirements = set(manifest["requirements"])
    assert "fontpkg==0.2.2" in requirements
    assert "fontpkg-noto-sans==2.15" in requirements

    notes = (ROOT / ".github" / "release-notes" / "0.4.5-beta40.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "Map Card already displays",
        "Noto Sans",
        "Home Assistant host",
        "Mägi",
        "Maja külg",
        "Üsküdar",
        "Øst",
    ):
        assert phrase in notes


def test_beta40_renderer_prefers_packaged_font() -> None:
    render = (COMPONENT / "map_snapshot_render.py").read_text(encoding="utf-8")
    assert "import fontpkg" in render
    assert 'fontpkg.path("Noto Sans")' in render
    assert "packaged = _packaged_snapshot_font_path()" in render
    assert "ImageFont.truetype(packaged, size=size)" in render
    assert render.index("ImageFont.truetype(packaged, size=size)") < render.index(
        "for candidate in _FONT_CANDIDATES:"
    )
