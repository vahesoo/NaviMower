"""Regression contracts for Navimower 0.4.1-beta3 raw-first telemetry."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta3_version_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta3"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta3.md").read_text()
    assert "Raw-first telemetry sensors" in notes
    assert "valid lower value" in notes


def test_public_progress_and_area_sensors_are_raw_first() -> None:
    source = (COMPONENT / "sensor.py").read_text()
    task = source.split('key="task_progress"', 1)[1].split('key="cutting_height"', 1)[0]
    assert 'value_fn=lambda d: d.get("mowing_progress")' in task
    assert 'mqtt_task_percentage' in task
    task_area = source.split('key="task_mowed_area"', 1)[1].split('key="map_mowed_area"', 1)[0]
    assert 'value_fn=lambda d: d.get("session_area")' in task_area
    assert 'mqtt_subtotal_area_m2' in task_area


def test_public_map_sensors_use_current_vendor_coverage_snapshot() -> None:
    source = (COMPONENT / "sensor.py").read_text()
    coverage = source.split('key="map_coverage"', 1)[1].split('key="task_mowed_area"', 1)[0]
    assert 'value_fn=lambda d: _vendor_map_coverage(d)' in coverage
    assert 'vendor_finished_area_over_vendor_area' in coverage
    area = source.split('key="map_mowed_area"', 1)[1].split('key="weekly_mowed_area"', 1)[0]
    assert 'value_fn=lambda d: _vendor_map_mowed_area(d)' in area
    assert 'private_cloud_coverage' in area


def test_stabilizer_does_not_rewrite_valid_vendor_regressions() -> None:
    source = (COMPONENT / "coordinator.py").read_text()
    block = source.split("def _stabilize_telemetry", 1)[1].split("def _schedule_state_save", 1)[0]
    assert "last_known_monotonic" not in block
    assert "last_known_zone_monotonic" not in block
    assert "cycle_reset_hold" not in block
    assert 'snapshot["cycle_value_reset_pending"] = False' in block
