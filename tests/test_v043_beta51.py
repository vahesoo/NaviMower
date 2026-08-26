"""Release-specific regression guards for Navimower 0.4.3-beta51."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta51_identity_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta51"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta51.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta51\n")


def test_beta51_shared_auth_list_diagnostics_are_value_free() -> None:
    source = (COMPONENT / "api" / "__init__.py").read_text(encoding="utf-8")
    assert "self._shared_auth_list_attempts" in source
    assert '"data_type": "none"' in source
    assert '"item_count": 0' in source
    assert '"first_item_keys": []' in source
    assert '"Navimower shared auth-list bootstrap did not yield uid: attempts=%s"' in source
    block = source[source.index("        if self._shared_auth_list_attempts:"):source.index("        if attempts:", source.index("        if self._shared_auth_list_attempts:"))]
    for secret in ("access_token", "refresh_token", "device_id", "vehicle_sn", "self._uid"):
        assert secret not in block
