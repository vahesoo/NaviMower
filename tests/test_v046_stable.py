"""Stable-release contracts for Navimower 0.4.6."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "navimower" / "manifest.json"
CHANGELOG = ROOT / "CHANGELOG.md"
NOTES = ROOT / ".github" / "release-notes" / "0.4.6.md"
WORKFLOW = ROOT / ".github" / "workflows" / "issue-publish-prerelease.yml"


def test_v046_stable_metadata() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.6"

    notes = NOTES.read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.6\n")
    for marker in (
        "Large History sessions no longer sit on the normal MQTT hot path",
        "Safer first install and restart recovery",
        "Schedule and Resume hardening",
        "Prepared History observability",
    ):
        assert marker in notes


def test_v046_stable_changelog() -> None:
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert "## 0.4.6 - 2026-09-28" in changelog
    assert "issue #406" in changelog
    assert "No 0.4.6 beta needs to be installed before this stable release." in changelog


def test_v046_stable_publisher_contract() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "PUBLISH NAVIMOWER RELEASE " in workflow
    assert 'MODE="stable"' in workflow
    assert "--prerelease=false --latest" in workflow
    assert "gh release create" in workflow
