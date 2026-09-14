"""Release-candidate documentation regressions for Navimower 0.4.4-beta36."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta36_release_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.4-beta36"

    notes = ROOT / ".github" / "release-notes" / "0.4.4-beta36.md"
    text = notes.read_text(encoding="utf-8")
    assert text.startswith("title: Navimower 0.4.4-beta36\n")
    assert "Runtime mower behavior is unchanged from beta35" in text
    assert "Gate areas and physical-gate example" in text
    assert "Map Card stable pairing remains intentionally undecided" in text


def test_readme_covers_current_044_contracts() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "**Map underlay**" in readme
    assert "docs/MAP_GEOREFERENCE_AND_UNDERLAYS.md" in readme
    assert "Mowing pause reason" in readme
    assert "internal vendor map zone IDs" in readme
    assert "exact polygons" in readme or "exact polygon" in readme
    assert "phased Map API" in readme
    assert "monotonic" in readme


def test_gate_guide_documents_owner_run_pattern_and_fallback() -> None:
    guide = (ROOT / "docs" / "GATE_AUTOMATION.md").read_text(encoding="utf-8")

    assert "one automation run owns the gate cycle" in guide
    assert "only the run that opened the gate is allowed to close it" in guide
    assert "state: \"closed\"" in guide
    assert "for:\n          seconds: 2" in guide
    assert "for:\n          seconds: 10" in guide
    assert "mode: parallel" in guide
    assert "Gate required + Custom Area" in guide
    assert "state_code') == '0211'" in guide


def test_current_supporting_docs_exist() -> None:
    for relative in (
        "docs/MAP_GEOREFERENCE_AND_UNDERLAYS.md",
        "docs/MULTI_MOWER.md",
        "docs/ARCHITECTURE.md",
        "docs/DIAGNOSTICS_PRIVACY.md",
    ):
        path = ROOT / relative
        assert path.is_file(), relative
        assert path.read_text(encoding="utf-8").strip(), relative
