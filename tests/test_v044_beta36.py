"""Release-candidate regressions for Navimower 0.4.4-beta38."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta38_release_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.4-beta38"

    notes = ROOT / ".github" / "release-notes" / "0.4.4-beta38.md"
    text = notes.read_text(encoding="utf-8")
    assert text.startswith("title: Navimower 0.4.4-beta38\n")
    assert "Repository privacy and interoperability cleanup" in text
    assert "brand/logo assets are intentionally unchanged" in text
    assert "runtime behavior from beta37" in text


def test_public_tree_no_longer_ships_retired_h5_research_helpers() -> None:
    assert not (COMPONENT / "maintenance_h5_discovery.py").exists()
    assert not (COMPONENT / "error_h5_discovery.py").exists()


def test_readme_is_user_facing_and_keeps_current_contracts() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "community interoperability project" in readme
    assert "**Map underlay**" in readme
    assert "docs/MAP_GEOREFERENCE_AND_UNDERLAYS.md" in readme
    assert "Mowing pause reason" in readme
    assert "internal vendor map zone IDs" in readme
    assert "exact polygons" in readme or "exact polygon" in readme
    assert "phased Map API" in readme
    assert "monotonic" in readme
    assert "navimower.export_raw_data" not in readme
    assert "reverse-engineered" not in readme.lower()
    assert "captured live" not in readme.lower()


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
