from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta20_release_notes_remain_available():
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
    assert "completed=self._session_completed(snapshot) if docked else None" not in coordinator
    assert "completed=None" in coordinator


def test_completion_metadata_and_freshness_diagnostics_remain_exposed():
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    zone_state = (COMPONENT / "zone_state.py").read_text(encoding="utf-8")
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    assert '"active_zone_progress_source_age"' in diagnostics
    assert '"coverage_source_age"' in diagnostics
    assert '"active_completion"' in history
    assert '"last_completed_source"' in zone_state
    assert '"last_completed_confirmation"' in zone_state
