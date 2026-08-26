"""Release-specific regression guards for Navimower 0.4.3-beta49."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta49_identity_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta49"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta49.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta49\n")


def test_beta49_failed_regional_login_logs_all_attempts_safely() -> None:
    source = (COMPONENT / "api" / "__init__.py").read_text(encoding="utf-8")
    assert "self._reported_region" in source
    assert "self._mower_login_attempts" in source
    assert '"Navimower private mower login failed: account_region=%s, "' in source
    assert '"reported_region=%s, attempts=%s"' in source
    assert "for host in self.mower_host_candidates" in source
    assert '"host": host' in source
    assert '"code": str(getattr(err, "code", "unknown"))' in source
    assert '"desc": str(getattr(err, "desc", "") or "")[:160]' in source
    warning_block = source[
        source.index("        if attempts:"):
        source.index("        if last_error is not None:", source.index("        if attempts:"))
    ]
    for secret in ("access_token", "refresh_token", "device_id", "vehicle_sn"):
        assert secret not in warning_block
