"""Release regressions for Navimower 0.5.0-beta14."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
CANONICAL = COMPONENT / "canonical_state.py"
AUTHORITY = COMPONENT / "canonical_authority_semantics.py"


def test_beta14_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta14"

    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta14.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Canonical PositionResolver",
        "map charging-station XY",
        "location.report_time",
        "docked_station_authority",
        "No Map Card update is required",
    ):
        assert marker in notes


def test_docked_station_authority_lives_in_canonical_resolver() -> None:
    source = CANONICAL.read_text(encoding="utf-8")
    assert 'source, age, stale = station, "map_station", None, False' in source
    assert 'docked=snapshot.get("docked") is True' in source
    assert 'fallback_reason = "docked_station_authority"' in source
    assert '"map_station_position_override_active"' in source


def test_cloud_position_age_uses_vendor_report_time_not_endpoint_fetch_age() -> None:
    source = AUTHORITY.read_text(encoding="utf-8")
    assert "cloud_report_age(_cloud_report_time(snapshot))" in source
    assert 'location.get("report_time")' in source
    assert '_private_endpoint_age("location")' not in source
    assert "station_position=station_position" in source
    assert "pending_activity=pending_activity" in source
