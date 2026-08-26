"""Release-specific regression guards for Navimower 0.4.3-beta49."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta49_release_notes_present() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta49.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta49\n")


def test_beta49_regional_login_still_tracks_attempts_safely() -> None:
    source = (COMPONENT / "api" / "__init__.py").read_text(encoding="utf-8")
    assert "self._reported_region" in source
    assert "self._mower_login_attempts" in source
    assert "for host in self.mower_host_candidates" in source
    assert '"host": host' in source
    assert 'account_region=%s' in source
    assert 'reported_region=%s' in source
    warning_start = source.index('            _LOGGER.warning(\n                "Navimower private mower login variants failed:')
    warning_end = source.index("        if self._shared_auth_list_attempts:", warning_start)
    warning_block = source[warning_start:warning_end]
    for secret in ("access_token", "refresh_token", "device_id", "vehicle_sn", "self._uid"):
        assert secret not in warning_block
