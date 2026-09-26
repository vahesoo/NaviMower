"""Permanent regressions for the MQTT/History performance hot path."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _source(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


def test_mqtt_and_poll_snapshots_do_not_materialize_full_active_trail() -> None:
    source = _source("coordinator.py")
    ast.parse(source)

    mqtt_start = source.index("    def ingest_mqtt_location")
    mqtt_end = source.index("    def ingest_mqtt_state", mqtt_start)
    mqtt = source[mqtt_start:mqtt_end]
    assert "active_points_xy()" not in mqtt
    assert 'snapshot.pop("trail", None)' in mqtt
    assert 'snapshot["trail_point_count"] = self.history.active_point_count()' in mqtt

    poll_start = source.index("    async def _async_update_data")
    poll_end = source.index("    @staticmethod\n    def _poll_interval_for_snapshot", poll_start)
    poll = source[poll_start:poll_end]
    assert "active_points_xy()" not in poll
    assert 'snapshot["trail_point_count"] = self.history.active_point_count()' in poll


def test_active_point_count_is_metadata_only() -> None:
    source = _source("history.py")
    ast.parse(source)
    start = source.index("    def active_point_count")
    end = source.index("    def card_materialization_diagnostics", start)
    block = source[start:end]
    assert 'active.get("points")' in block
    assert "len(points)" in block
    assert "_card_points" not in block
    assert "simplify_xy_points" not in block


def test_prepared_live_source_capture_is_off_main_thread() -> None:
    source = _source("prepared_render_model.py")
    ast.parse(source)
    assert "await self.hass.async_add_executor_job(self._live_source)" in source


def test_performance_diagnostics_expose_ingest_and_materialization_cost() -> None:
    diagnostics = _source("diagnostics.py")
    assert '"coordinator_ingest_count"' in diagnostics
    assert '"coordinator_ingest_last_ms"' in diagnostics
    assert '"coordinator_ingest_max_ms"' in diagnostics
    assert '"card_materialization"' in diagnostics
