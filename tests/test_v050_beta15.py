"""Historical regressions retained from Navimower 0.5.0-beta15."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta15_release_notes_are_retained() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta15.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Work mode persistence",
        'robot: `"02" / "03" / "04"`',
        "cloud: `2 / 3 / 4`",
        "station-as-position",
        "persistence verification",
    ):
        assert marker in notes


def test_station_position_override_is_removed() -> None:
    canonical = (COMPONENT / "canonical_state.py").read_text(encoding="utf-8")
    authority = (COMPONENT / "canonical_authority_semantics.py").read_text(
        encoding="utf-8"
    )
    assert '"map_station"' not in canonical
    assert "station_position" not in canonical
    assert "docked_station_authority" not in canonical
    assert "def _station_position" not in authority
    assert "cloud_report_age(_cloud_report_time(snapshot))" in authority


def test_setting_persistence_verification_remains_available() -> None:
    source = (COMPONENT / "setting_write.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert "SETTING_PERSISTENCE_DELAY_SECONDS = 75.0" in source
    assert "_setting_write_verification" in source
    assert '"setting_write_verification"' in diagnostics
