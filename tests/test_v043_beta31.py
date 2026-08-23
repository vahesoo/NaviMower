"""Regression guards for 0.4.3-beta31 Custom Area UI hotfix."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_custom_area_flow_has_visible_guidance_in_strings_and_english_translation() -> None:
    for path in (COMPONENT / "strings.json", COMPONENT / "translations" / "en.json"):
        data = _load(path)
        options = data["options"]
        steps = options["step"]
        assert steps["init"]["menu_options"]["custom_areas"] == "Custom areas"
        assert steps["custom_areas"]["menu_options"]["custom_area_add"] == "Add custom area"
        assert "Now open the Navimow app and Map editor" in steps["custom_area_detect"]["description"]
        assert "{baseline_revision}" in steps["custom_area_detect"]["description"]
        assert "After it is saved, you can delete the temporary off-limit area" in steps["custom_area_name"]["description"]
        for key in (
            "custom_area_map_not_available",
            "custom_area_refresh_failed",
            "custom_area_not_detected",
            "custom_area_multiple_detected",
            "custom_area_invalid",
            "custom_area_duplicate",
        ):
            assert options["error"][key]
        assert options["abort"]["no_custom_areas"]


def test_beta31_release_notes_remain_available() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta31.md").read_text(encoding="utf-8")
    assert "0.4.3-beta31" in notes
