import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta20_identity():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta20"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta20.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta20")


def test_navimower_schedule_dispatches_one_zone_at_a_time():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    send = source[source.index("    async def _async_send_mow"):]
    assert "encode_partition_ids([zone_id])" in send
    assert "requested_zone_ids=[zone_id]" in send
    assert "resolved_zone_ids=[zone_id]" in send
    assert "completion_advanced(" in source


def test_docking_only_finalizes_history():
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    dock_start = history.index("            if docked and not cutting:")
    dock_end = history.index("    # Backward-compatible alias", dock_start)
    dock = history[dock_start:dock_end]
    assert "_finish_active_locked" in dock
    assert "last_completed_at" not in dock
    assert "last_completed_progress" not in dock
    assert "completed=self._session_completed(snapshot) if docked else None" not in coordinator
    assert "completed=None" in coordinator


def test_completion_is_fresh_current_cycle_and_fail_closed():
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    assert "_COMPLETION_EVIDENCE_MAX_AGE_SECONDS = 30.0" in history
    assert '"last_known_zone"' not in history[history.index("_ACTIVE_ZONE_COMPLETION_SOURCES"):history.index("SESSION_DETAIL_POINT_FORMAT")]
    assert '"private_zone_coverage"' in history
    assert '"guarded_large_progress_jump"' in history
    assert '"waiting_for_second_fresh_sample"' in history
    assert '"awaiting_second_fresh_sample"' in history
    assert '"returning_after_95"' not in history
    assert "vendor_end_ms >= cycle_start_ms" in history
    assert '"last_completed_source"' in history
    assert '"last_completed_confirmation"' in history
    assert 'self._private_endpoint_age("path_info_time")' in coordinator
    assert "_mqtt_route_progress_last_update" in coordinator
    assert "_mqtt_work_progress_last_update" in coordinator
    assert "_mqtt_task_progress_last_update" in coordinator
    assert 'snapshot.get("work_progress_source_age")' in coordinator


def test_completion_diagnostics_expose_source_and_freshness():
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    zone_state = (COMPONENT / "zone_state.py").read_text(encoding="utf-8")
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    assert '"active_zone_progress_source_age"' in diagnostics
    assert '"coverage_source_age"' in diagnostics
    assert '"active_completion"' in history
    assert '"last_completed_source"' in zone_state
    assert '"last_completed_confirmation"' in zone_state
