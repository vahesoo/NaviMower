import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta21_identity():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta21"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta21.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta21")


def test_last_completed_is_vendor_zone_coverage_authoritative():
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    start = history.index("    def _confirm_coverage_completions_locked")
    end = history.index("    def cycle_diagnostics", start)
    completion = history[start:end]
    assert 'row.get("pct")' in completion
    assert '"private_zone_coverage"' in completion
    assert 'if progress < 100:' in completion
    assert '"coverage_100_after_incomplete"' in completion
    assert '"coverage_100_transition"' in completion
    assert '"coverage_100_recent_vendor_cycle"' in completion
    assert '"coverage_100_without_current_cycle_evidence"' in completion
    assert '"last_completed_progress": 100' in completion
    assert '"last_completed_cycle_id": active.get("id")' in completion
    assert "mqtt_map_work_position" not in completion
    assert "mqtt_task_percentage" not in completion
    assert "current_cycle_cloud_end" not in history
    assert "waiting_for_second_fresh_sample" not in history


def test_target_transition_keeps_previous_zone_armed_until_coverage_100():
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    start = history.index("    def _confirm_coverage_completions_locked")
    end = history.index("    def cycle_diagnostics", start)
    completion = history[start:end]
    assert 'seen_target.add(current_target)' in completion
    assert 'seen_target.add(physical_zone)' in completion
    assert 'relevant = sorted(seen_target | seen_incomplete)' in completion
    assert 'seen_incomplete.add(zone_id)' in completion
    assert 'active["task_zone_seen_target"] = sorted(seen_target)' in completion
    assert 'active["task_zone_seen_incomplete"] = sorted(seen_incomplete)' in completion


def test_private_live_100_and_task_100_cannot_directly_complete():
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    update_start = history.index("    def update_zone_history")
    update_end = history.index("    def update_from_snapshot", update_start)
    update = history[update_start:update_end]
    assert "last_completed_at" not in update
    assert "active_zone_progress_source" in update
    assert "task_progress_source" in update
    assert "completion is owned by vendor coverage arbitration" in update


def test_cloud_end_time_is_timestamp_only_after_coverage_100():
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    start = history.index("    def _confirm_coverage_completions_locked")
    end = history.index("    def cycle_diagnostics", start)
    completion = history[start:end]
    progress_gate = completion.index("if progress < 100:")
    completion_write = completion.index('"last_completed_at"')
    end_time_use = completion.index("vendor_end_ms")
    assert progress_gate < completion_write
    assert end_time_use < completion_write
    assert "end_time + >=95" not in completion
