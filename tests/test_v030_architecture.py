from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_release_version_and_map_schema() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.3.4"
    const_source = (COMPONENT / "const.py").read_text()
    assert "MAP_API_SCHEMA_VERSION: Final = 5" in const_source


def test_clean_public_sensor_model() -> None:
    source = (COMPONENT / "sensor.py").read_text()
    for key in (
        'key="task_progress"',
        'key="route_progress"',
        'key="map_coverage"',
        'key="task_mowed_area"',
        'key="map_mowed_area"',
        'key="weekly_mowed_area"',
        'key="map_area"',
        'key="last_map_mowed"',
        'key="last_map_completed"',
    ):
        assert key in source
    static_source = source.split("class ZoneMetricDescription", 1)[0]
    for old_key in (
        'key="mowing_progress"',
        'key="coverage"',
        'key="session_area"',
        'key="weekly_area"',
        'key="total_area"',
        'key="mow_route_progress"',
    ):
        assert old_key not in static_source


def test_zone_sensor_defaults_and_api_payload() -> None:
    sensor_source = (COMPONENT / "sensor.py").read_text()
    assert 'key="coverage"' in sensor_source
    assert "enabled_default=True" in sensor_source
    assert sensor_source.count("enabled_default=True") == 1
    assert "class NavimowerZoneSensor" in sensor_source

    coordinator_source = (COMPONENT / "coordinator.py").read_text()
    for field in (
        '"zone_states"',
        '"zone_states_revision"',
        '"totals"',
        '"daily_trails"',
        '"daily_trails_revision"',
    ):
        assert field in coordinator_source


def test_history_filters_stale_progress_and_empty_stubs() -> None:
    source = (COMPONENT / "history.py").read_text()
    assert "cycle_reset_pending" in source
    assert "active_zone_progress" in source
    assert "active_task_progress" not in source
    assert "_is_provisional_session" in source
    assert "_discard_active_locked" in source
    assert "_async_remove_empty_completed_sessions" in source


def test_task_and_active_zone_progress_are_kept_separate() -> None:
    coordinator_source = (COMPONENT / "coordinator.py").read_text()
    assert '("mqtt_task_percentage", mqtt_progress["mowing_percentage"])' in coordinator_source
    assert '("mqtt_map_work_position", mqtt_progress["work_progress"])' in coordinator_source
    assert '("mqtt_route_progress", mqtt_progress["route_progress"])' in coordinator_source
    task_block = coordinator_source.split(
        "# Task progress and active-zone progress are separate vendor counters.", 1
    )[1].split("def _valid_zone_id", 1)[0]
    assert 'mqtt_progress["work_progress"]' not in task_block
    assert 'mqtt_progress["route_progress"]' not in task_block
