"""Release regression for Navimower 0.4.5-beta36."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta36_release_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    assert version.startswith("0.4.5-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 36

    notes = (ROOT / ".github" / "release-notes" / "0.4.5-beta36.md").read_text(
        encoding="utf-8"
    )
    assert notes.startswith("title: Navimower 0.4.5-beta36\n")
    for phrase in (
        "Production cleanup",
        "Download diagnostics",
        "privacy/interoperability regression",
        "Passive protocol discovery",
        "Google Map Tiles credentials",
    ):
        assert phrase in notes
