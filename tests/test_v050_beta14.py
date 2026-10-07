"""Historical beta14 notes plus beta15 rollback coverage."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
CANONICAL = COMPONENT / "canonical_state.py"
AUTHORITY = COMPONENT / "canonical_authority_semantics.py"


def test_beta14_release_notes_are_retained() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta14.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Canonical PositionResolver",
        "map charging-station XY",
        "location.report_time",
        "docked_station_authority",
    ):
        assert marker in notes


def test_beta15_retires_station_as_public_mower_position() -> None:
    canonical = CANONICAL.read_text(encoding="utf-8")
    authority = AUTHORITY.read_text(encoding="utf-8")
    assert '"map_station"' not in canonical
    assert "docked_station_authority" not in canonical
    assert "map_station_position_override_active" not in canonical
    assert "station_position" not in canonical
    assert "def _station_position" not in authority
    assert "station_position=station_position" not in authority


def test_cloud_position_age_still_uses_vendor_report_time() -> None:
    source = AUTHORITY.read_text(encoding="utf-8")
    assert "cloud_report_age(_cloud_report_time(snapshot))" in source
    assert 'location.get("report_time")' in source
    assert '_private_endpoint_age("location")' not in source
