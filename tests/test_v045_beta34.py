"""Regression contracts for Navimower 0.4.5-beta34."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta34_version() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert str(manifest["version"]).startswith("0.4.5-beta")


def test_beta34_registers_continue_last_ordered_run_action() -> None:
    services = (COMPONENT / "services.py").read_text(encoding="utf-8")
    yaml = (COMPONENT / "services.yaml").read_text(encoding="utf-8")

    assert 'SERVICE_CONTINUE_LAST_ORDERED_RUN = "continue_last_ordered_run"' in services
    assert "CONTINUE_LAST_ORDERED_RUN_SCHEMA = DEVICE_ONLY_SCHEMA" in services
    assert "async def _continue_last_ordered_run(" in services
    assert 'source="navimower.continue_last_ordered_run"' in services
    assert "partition_setup = mow_setup(reset=False, ordered=True)" in services
    assert "coordinator.remaining_last_ordered_run_zone_ids()" in services
    assert "await coordinator.async_refresh_last_ordered_run_completion()" in services
    assert "no mowing command was sent" in services
    assert "SERVICE_CONTINUE_LAST_ORDERED_RUN" in services

    assert "continue_last_ordered_run:" in yaml
    assert "Only unfinished zones are sent to the mower" in yaml
    assert "reset=false" in yaml


def test_beta34_persists_ordered_run_across_dock_and_restart() -> None:
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")

    assert "self._last_ordered_run: dict[str, Any] | None = None" in coordinator
    assert 'cached.get("last_ordered_run")' in coordinator
    assert '"last_ordered_run": last_ordered_run_snapshot(' in coordinator
    assert "def remaining_last_ordered_run_zone_ids(" in coordinator
    assert "async def async_refresh_last_ordered_run_completion(" in coordinator
    assert 'self._endpoint_status.setdefault(\n            "path_info_time"' in coordinator
    assert '"navimower.continue_last_ordered_run",' in coordinator
    assert "start_last_ordered_run(" in coordinator
    assert "update_last_ordered_run(" in coordinator


def test_beta34_exposes_ordered_run_state_on_task_progress() -> None:
    sensor = (COMPONENT / "sensor.py").read_text(encoding="utf-8")

    assert '"last_ordered_run": d.get("last_ordered_run")' in sensor
    assert '"last_ordered_zone_ids"' in sensor
    assert '"last_ordered_completed_zone_ids"' in sensor
    assert '"last_ordered_remaining_zone_ids"' in sensor
    assert '"last_ordered_run_resumable"' in sensor


def test_beta34_completion_uses_confirmed_timestamp_not_display_threshold() -> None:
    ordered = (COMPONENT / "ordered_run.py").read_text(encoding="utf-8")

    assert 'row.get("last_completed_at")' in ordered
    assert "completed_ms >= started_ms" in ordered
    assert "coverage >= CONFIRMED_COMPLETION_PCT" in ordered
    assert "if not reset:" in ordered
    assert "remaining_zone_ids" in ordered
