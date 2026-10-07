"""Release regressions for Navimower 0.5.0-beta15."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta15_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta15"
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


def test_work_mode_uses_asymmetric_transport_encoding() -> None:
    source = (COMPONENT / "select.py").read_text(encoding="utf-8")
    block = source.split('key="work_mode"', 1)[1].split(
        "NavimowSelectDescription(", 1
    )[0]
    assert '"standard": 2' in block
    assert '"efficient": 3' in block
    assert '"precision": 4' in block
    assert "robot_numeric=True" not in block
    assert 'robot_value = f"{value:02d}"' in source
    assert "cloud_value = value" in source


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


def test_setting_writes_have_late_persistence_verification() -> None:
    source = (COMPONENT / "setting_write.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert "SETTING_PERSISTENCE_DELAY_SECONDS = 75.0" in source
    assert "def _finish_verification(" in source
    assert '"status": "confirmed" if not failed else "reverted"' in source
    assert "_setting_write_verification" in source
    assert '"setting_write_verification"' in diagnostics
