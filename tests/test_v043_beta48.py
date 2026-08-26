"""Release-specific regression guards for Navimower 0.4.3-beta48."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta48_identity_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta48"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta48.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta48\n")


def test_beta48_mower_login_uses_concrete_discovery_path() -> None:
    source = (COMPONENT / "api" / "client.py").read_text(encoding="utf-8")
    mower_login = source[
        source.index("    def mower_login"):
        source.index("    @staticmethod\n    def _extract_uid", source.index("    def mower_login"))
    ]
    assert 'self._record_discovery("/user/user/login", None, code)' in mower_login
    assert "self._record_discovery(path, None, code)" not in mower_login
