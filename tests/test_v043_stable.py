"""Stable 0.4.3 release regression coverage."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_stable_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3"

    notes = (ROOT / ".github" / "release-notes" / "0.4.3.md").read_text(
        encoding="utf-8"
    )
    assert notes.startswith("title: Navimower 0.4.3")
    for marker in (
        "Navimower Schedule",
        "current_cycle_render",
        "Custom Areas",
        "Reset schedule progress",
        "Gate required",
        "Navimower Map Card 0.3.5 requires Navimower integration 0.4.3 or newer",
    ):
        assert marker in notes


def test_stable_release_publisher_supports_stable_and_prerelease() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "issue-publish-prerelease.yml"
    ).read_text(encoding="utf-8")
    assert "PUBLISH NAVIMOWER RELEASE " in workflow
    assert "PUBLISH NAVIMOWER PRERELEASE " in workflow
    assert 'MODE="stable"' in workflow
    assert 'MODE="prerelease"' in workflow
    assert "--prerelease=false --latest" in workflow
    assert 'gh release create "${TAG}"' in workflow
    assert "--latest" in workflow


def test_stable_docs_cover_current_setup_and_gate_example() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Settings -> Devices & services -> Navimower -> Configure" in readme
    assert "temporarily **merge" in readme.lower() or "temporarily merge" in readme.lower()
    assert "Navimower Map Card 0.3.5 requires Navimower integration 0.4.3 or newer" in readme

    gate = (ROOT / "docs" / "GATE_AUTOMATION.md").read_text(encoding="utf-8")
    assert "Navimower Schedule" in gate
    assert "one mowing zone" in gate
    assert "extends **slightly into the mowing zone**" in gate
    assert "Gate required" in gate
    assert "Custom Area" in gate


def test_stable_multi_mower_guide_matches_current_setup() -> None:
    guide = (ROOT / "docs" / "MULTI_MOWER.md").read_text(encoding="utf-8")
    assert "selects it automatically" in guide
    assert "Download diagnostics" in guide
    assert "one config entry per mower" in guide
