"""Release-specific regression guards for Navimower 0.4.3-beta53."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta53_release_notes_exist() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta53.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta53\n")


def test_beta53_mower_login_logs_plain_and_signed_structure_only() -> None:
    source = (COMPONENT / "api" / "__init__.py").read_text(encoding="utf-8")
    block = source[
        source.index("    def _login_response_row"):
        source.index("    def bootstrap_shared_auth_list", source.index("    def _login_response_row"))
    ]
    assert '"data_keys": data_keys' in block
    assert '"top_level_keys": top_level_keys' in block
    login = source[
        source.index("    def mower_login"):
        source.index("    def errors", source.index("    def mower_login"))
    ]
    assert '"plain"' in login
    assert '"signed"' in login
    assert 'Navimower private mower login variants failed:' in login
    warning = source[source.index("            attempt_text = "):source.index("        if self._shared_auth_list_attempts:")]
    for secret in ("self._tokens.access_token,", "self._tokens.refresh_token,", "self._tokens.uuid,"):
        assert secret not in warning
