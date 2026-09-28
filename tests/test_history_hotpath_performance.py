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


def test_active_session_hot_paths_use_metadata_and_incremental_tail() -> None:
    coordinator = _source("coordinator.py")
    zone_ledger = _source("zone_ledger_semantics.py")
    coordinator_semantics = _source("coordinator_semantics.py")
    schedule = _source("navimower_schedule.py")
    services = _source("services.py")

    refresh_start = coordinator.index("    def _refresh_zone_model")
    refresh_end = coordinator.index("    def _session_completed", refresh_start)
    refresh = coordinator[refresh_start:refresh_end]
    assert "active_session_metadata()" in refresh
    assert "self.history.active_session," not in refresh

    assert "active_session_metadata()" in zone_ledger
    assert "self.history.active_session_tail(after_ms=last_stamp)" in coordinator_semantics
    assert "history.active_session_metadata()" in schedule
    assert "coordinator.history.active_session_metadata()" in services


def test_active_session_access_diagnostics_are_point_count_only() -> None:
    history = _source("history.py")
    diagnostics = _source("diagnostics.py")
    assert "def active_session_metadata" in history
    assert "def active_session_tail" in history
    assert "def active_session_access_diagnostics" in history
    assert '"active_point_count"' in history
    assert '"last_copied_point_count"' in history
    assert '"last_points_per_second"' in history
    assert '"active_session_access"' in diagnostics


def test_prepared_live_uses_minimal_render_snapshot_not_full_session_copy() -> None:
    prepared = _source("prepared_render_model.py")
    history = _source("history.py")
    start = prepared.index("    def _live_source")
    end = prepared.index("    async def _build_live", start)
    block = prepared[start:end]
    assert "active_session_render_snapshot" in block
    assert 'getattr(history, "active_session", None)' not in block
    assert '"active_session": deepcopy(active_session)' not in block
    assert "def active_session_render_snapshot" in history
    assert '"render_snapshots"' in history
