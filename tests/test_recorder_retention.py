"""beta32: Recorder churn fix and quiet-season History retention safeguards."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"

# Time-dependent freshness is kept in coordinator data and cached diagnostics.
# The entity state/attributes must not change simply because time has passed.
VOLATILE_ATTRS = {
    "source_age", "mqtt_age", "weather_age_s", "mqtt_pose_age",
    "private_poll_age", "last_location_message_age", "core_age",
    "target_age_seconds", "pose_age", "vendor_source_age",
    "render_age_seconds", "position_age",
}


def _source(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


def _tree(name: str) -> ast.Module:
    return ast.parse(_source(name))


def _dict_keys(node: ast.AST) -> set[str]:
    result: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Dict):
            result.update(
                key.value for key in child.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
    return result


def test_sensor_attributes_do_not_publish_clock_ages() -> None:
    source = _source("sensor.py")
    tree = _tree("sensor.py")
    descriptions = next(
        item for item in tree.body
        if isinstance(item, ast.AnnAssign)
        and isinstance(item.target, ast.Name) and item.target.id == "SENSORS"
    )
    # Some dedicated opt-in diagnostic sensors intentionally publish age as
    # their *state*. They must not put another changing clock in attributes.
    assert not (_dict_keys(descriptions.value) & VOLATILE_ATTRS)
    assert 'key="pose_age"' in source
    assert 'key="private_poll_age"' in source
    assert "entity_registry_enabled_default=False" in source


def test_optional_sensor_installers_keep_freshness_without_age_churn() -> None:
    notification = _source("notification_feed.py")
    block = notification.split("def _install_notification_sensor()", 1)[1]
    attrs = block.split("    def attrs(", 1)[1].split("    platform.SENSORS", 1)[0]
    assert '"source_age":' not in attrs
    assert '"vendor_source_age":' not in attrs
    assert '"recent":' in attrs
    assert '"source_age": data.get("notification_source_age")' in _source("diagnostics.py")

    physical = _source("navigation_fallback.py").split("def physical_attrs(", 1)[1].split(
        "    platform.SENSORS", 1
    )[0]
    assert '"position_age":' not in physical
    assert '"stale":' in physical
    assert '"mqtt_pose_valid":' in physical

    paused = _source("mowing_pause_status.py")
    assert 'if key != "weather_age_s"' in paused
    assert '"weather_fresh"' in paused

    custom = _source("binary_sensor.py").split("class NavimowerCustomAreaBinarySensor", 1)[1].split(
        "class NavimowerAggregateGateRequiredBinarySensor", 1
    )[0]
    assert '"position_age":' not in custom
    assert '"state_confirmed":' in custom


def test_large_live_payloads_not_recorded_but_still_available_to_cards() -> None:
    sensor = _source("sensor.py")
    assert '_unrecorded_attributes = frozenset({"recent"})' in sensor
    assert '_unrecorded_attributes = frozenset({MATCH_ALL})' in sensor
    assert '"recent": deepcopy(' in _source("notification_feed.py")
    assert '"api_path": f"/api/navimower/map/' in sensor
    assert 'super()._handle_coordinator_update()' in _source("image.py")
    assert '"render_age_seconds":' not in _source("image.py")
    assert '"weather_age_s":' not in _source("lawn_mower.py")
    assert '"mqtt_pose_age":' not in _source("lawn_mower.py")


def test_diagnostics_retains_age_evidence_without_adding_requests() -> None:
    diagnostics = _source("diagnostics.py")
    for key in (
        '"session_area_source_age"',
        '"current_physical_zone_position_age"',
        '"render_age_seconds"',
        '"weather_state_age"',
        '"notification_source_age"',
    ):
        assert key in diagnostics
    assert '"cached_only": True' in diagnostics


def test_daily_retention_is_entry_scoped_and_cancelled_on_unload() -> None:
    setup = _source("__init__.py")
    history = _source("history.py")
    archive = _source("session_archive.py")
    assert "async def _async_history_retention_housekeeping(" in setup
    assert "await asyncio.sleep(24 * 60 * 60)" in setup
    assert "await coordinator.history.async_checkpoint()" in setup
    assert "await archive._async_prune_archive_stores()" in setup
    assert "history_retention_task.cancel()" in setup
    assert "_async_history_retention_housekeeping(coordinator)" in setup
    assert "if self.retention_days <= 0:" in history
    assert "session_id != self._active_id and stamp and stamp < cutoff_ms" in history
    assert "await self._session_store_for(session_id).async_remove()" in history
    assert "stale = sorted(self._known_store_ids - retained)" in archive
    assert "await _archive_store(self.hass, self.entry_id, session_id).async_remove()" in archive
    # No changes to command payloads, MQTT polling or frontend required.
    assert "navimower.mow_paused" in _source("services.py")
    assert "RESUME_STRATEGY_VENDOR" in _source("task_resume.py")
