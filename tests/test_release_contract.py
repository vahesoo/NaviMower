"""Release metadata checks that do not need edits for each new beta."""
from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components/navimower/manifest.json"


def test_manifest_version_and_release_notes_match() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    version = manifest["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version)
    notes = ROOT / ".github/release-notes" / f"{version}.md"
    assert notes.is_file(), f"Missing release notes for {version}"
    assert notes.read_text(encoding="utf-8").startswith(f"title: Navimower {version}\n")
