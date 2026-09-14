"""Release contract for Navimower 0.4.4-beta39."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta39_release_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.4-beta39"

    notes = ROOT / ".github" / "release-notes" / "0.4.4-beta39.md"
    text = notes.read_text(encoding="utf-8")
    assert text.startswith("title: Navimower 0.4.4-beta39\n")
    for phrase in (
        "Paused-returning Start is now resume-safe",
        "vendor no-fix `0/0` pair",
        "decoded map",
        "Existing schedules can always be switched off",
        "redaction contract is bumped to version 4",
        "navimow_pro` v0.6.0",
    ):
        assert phrase in text
