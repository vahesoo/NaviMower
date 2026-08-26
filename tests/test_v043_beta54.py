"""Regression guards for Navimower 0.4.3-beta54."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta54_release_notes_exist() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta54.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta54\n")


def test_beta54_passport_keeps_raw_region() -> None:
    source = (COMPONENT / "api" / "passport.py").read_text(encoding="utf-8")
    assert 'region=str(raw_region) if raw_region else ""' in source
    assert 'return raw_region or None' in source
    assert 'tokens.region = tokens.region or account_region' in source
    assert 'new.region = raw_region' in source


def test_beta54_mower_login_uses_raw_region_but_routes_canonically() -> None:
    source = (COMPONENT / "api" / "__init__.py").read_text(encoding="utf-8")
    assert 'self._region = canonical_region(reported)' in source
    assert 'raw_region = self._tokens.region or self._reported_region or self._region' in source
    assert '"region": raw_region' in source
    assert '"region": self._tokens.region or self._reported_region or state.get("region") or self._region' in source
