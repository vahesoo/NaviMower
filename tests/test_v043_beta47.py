"""Release-specific regression guards for Navimower 0.4.3-beta47."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta47_release_notes_exist() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta47.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta47\n")


def test_beta47_low_battery_fallback_contract() -> None:
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    low_battery = source[
        source.index("    async def _evaluate_low_battery_resume"):
        source.index("    def _pending_mow_confirmed", source.index("    async def _evaluate_low_battery_resume"))
    ]
    assert "_LOW_BATTERY_RESUME_GRACE_SECONDS = 180" in source
    assert 'get("charging_limit")' in source
    assert "_vendor_charging" not in low_battery
    assert "if not self._window_open_now():" in low_battery
    assert 'source="navimower_schedule_charge_limit_fallback"' in low_battery
    assert 'continue_source="navimower_schedule_charge_limit_continue_fallback"' in low_battery
    assert "reset=True" not in low_battery
