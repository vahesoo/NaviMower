"""Release-specific regression guards for Navimower 0.4.3-beta52."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta52_identity_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta52"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta52.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta52\n")


def test_beta52_shared_bootstrap_tries_login_style_credentials() -> None:
    source = (COMPONENT / "api" / "__init__.py").read_text(encoding="utf-8")
    block = source[
        source.index("    def bootstrap_shared_auth_list"):
        source.index("    def mower_login", source.index("    def bootstrap_shared_auth_list"))
    ]
    for field in ('"uuid": self._tokens.uuid', '"token": self._tokens.access_token', '"refresh_token": self._tokens.refresh_token', '"region": self._region'):
        assert field in block
    assert '"login_style"' in block
    assert '"login_style_plus_access_token"' in block
    assert 'access_token="", uid=""' in block
    assert 'access_token=self._tokens.access_token' in block
    assert 'self._raw("/vehicle/vehicle/auth-list", body)' in block
    assert '"variant": variant' in block
