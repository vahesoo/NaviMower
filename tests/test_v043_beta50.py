"""Release-specific regression guards for Navimower 0.4.3-beta50."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta50_identity_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta50"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta50.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta50\n")


def test_beta50_shared_auth_list_bootstrap_contract() -> None:
    source = (COMPONENT / "api" / "__init__.py").read_text(encoding="utf-8")
    assert "def bootstrap_shared_auth_list" in source
    assert 'self._raw("/vehicle/vehicle/auth-list", body)' in source
    assert 'access_token=self._tokens.access_token' in source
    assert 'uid=""' in source
    assert 'items[0].get("auth_uid") or items[0].get("authUid")' in source
    assert '"shared_auth_list_probe"' in source
    assert "self.set_host(host, source=source)" in source
    mower_login = source[source.index("    def mower_login"):source.index("    def errors", source.index("    def mower_login"))]
    assert "shared_items = self.bootstrap_shared_auth_list()" in mower_login
    assert "if shared_items and self._uid:" in mower_login
    assert "return self._uid" in mower_login
