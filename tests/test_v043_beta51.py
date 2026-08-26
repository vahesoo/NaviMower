"""Release-specific regression guards for Navimower 0.4.3-beta51."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta51_release_notes_exist() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta51.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta51\n")


def test_beta51_shared_auth_list_diagnostics_are_value_free() -> None:
    source = (COMPONENT / "api" / "__init__.py").read_text(encoding="utf-8")
    assert "self._shared_auth_list_attempts" in source
    assert '"data_type": "none"' in source
    assert '"item_count": 0' in source
    assert '"first_item_keys": []' in source
    assert '"Navimower shared auth-list bootstrap did not yield uid: attempts=%s"' in source
    start = source.index("        if self._shared_auth_list_attempts:")
    end = source.index("        if last_error is not None:", start)
    block = source[start:end]
    for secret in ("access_token", "refresh_token", "device_id", "vehicle_sn", "self._uid"):
        assert secret not in block
