"""Release contract for Navimower 0.4.6-beta4."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta4_version() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.6-beta4"


def test_beta4_scope_is_active_session_hotpath_hardening() -> None:
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    notes = (ROOT / ".github" / "release-notes" / "0.4.6-beta4.md").read_text(
        encoding="utf-8"
    )

    assert "def active_session_metadata" in history
    assert "def active_session_tail" in history
    assert "def active_session_access_diagnostics" in history
    assert "active_session_metadata()" in coordinator
    assert '"active_session_access"' in diagnostics
    assert notes.startswith("title: Navimower 0.4.6-beta4")
