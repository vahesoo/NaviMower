"""Regression contracts for Navimower 0.4.5-beta39 snapshots."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta39_release_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.5-beta39"
    notes = (ROOT / ".github" / "release-notes" / "0.4.5-beta39.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "Map snapshot dark",
        "disabled by default",
        "Only snapshot entities that are actually enabled",
        "Unicode-capable TrueType font",
        "ä ö ü õ Ø ı ş Ł ą",
    ):
        assert phrase in notes


def test_beta39_dark_snapshot_is_opt_in_and_independently_cached() -> None:
    image = (COMPONENT / "image.py").read_text(encoding="utf-8")
    manager = (COMPONENT / "map_snapshot.py").read_text(encoding="utf-8")
    services = (COMPONENT / "services.py").read_text(encoding="utf-8")

    assert "class NavimowMapSnapshotDarkImage" in image
    assert "_attr_entity_registry_enabled_default = False" in image
    assert 'get_map_snapshot_manager(coordinator, "dark")' in image

    assert '"map_snapshot_dark_manager"' in manager
    assert "def active(self) -> bool:" in manager
    assert "if not self.active:" in manager
    assert "def active_map_snapshot_managers" in manager

    assert "active_map_snapshot_managers(coordinator)" in services
    assert "await asyncio.gather(" in services


def test_beta39_unicode_labels_use_true_type_fallbacks() -> None:
    render = (COMPONENT / "map_snapshot_render.py").read_text(encoding="utf-8")
    assert "_FONT_CANDIDATES" in render
    assert "ImageFont.truetype(candidate, size=size)" in render
    assert 'unicodedata.normalize("NFC"' in render
    assert '"DejaVuSans.ttf"' in render
