import json
from pathlib import Path

from custom_components.navimower.zone_state import build_zone_model

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta22_release_notes_remain_available():
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta22.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta22")


def test_active_zone_live_progress_wins_over_reset_zero_cache():
    rows, totals = build_zone_model(
        map_zones=[{"id": 24, "name": "Street", "area": 82.0}],
        zone_details=[{
            "id": 24,
            "name": "Street",
            "area_m2": 82.0,
            "progress": 33,
            "vendor_percentage": 32,
            "progress_source": "mqtt_map_work_position",
        }],
        coverage={"zones": [{
            "id": 24,
            "name": "Street",
            "area": 82.0,
            "finished": 26.24,
            "pct": 32,
        }]},
        zone_history={},
        active_session={
            "id": "cycle-1",
            "zone_ids": [24],
            "visited_zone_ids": [24],
            "task_zone_progress": {"24": 0},
        },
        active_zone_id=24,
        task_progress_pct=33,
        task_mowed_area_m2=27.06,
        task_progress_source="mqtt_task_percentage",
        task_area_source="mqtt_location",
    )
    street = rows[0]
    assert street["coverage_pct"] == 33.0
    assert street["task_progress_pct"] == 33.0
    assert street["mowed_area_m2"] == 27.06
    assert street["progress_source"] == "mqtt_map_work_position"
    assert totals["task_progress_pct"] == 33.0


def test_session_display_cache_tracks_coverage_and_fresh_mqtt_only():
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    completion_start = history.index("    def _confirm_coverage_completions_locked")
    completion_end = history.index("    def cycle_diagnostics", completion_start)
    completion = history[completion_start:completion_end]
    assert 'active.setdefault("task_zone_progress", {})[zone_key] = progress' in completion
    assert 'active.setdefault("task_zone_progress", {})[zone_key] = 100' in completion

    update_start = history.index("    def update_zone_history")
    update_end = history.index("    def update_from_snapshot", update_start)
    update = history[update_start:update_end]
    assert '"mqtt_map_work_position"' in update
    assert '"mqtt_route_progress"' in update
    assert 'live_source in {' in update
    assert 'str(live_zone_id)' in update
    assert '"private_map_work_position"' not in update
    assert "last_completed_at" not in update


def test_active_zone_projection_preserves_existing_safety_guards():
    source = (COMPONENT / "zone_state.py").read_text(encoding="utf-8")
    assert "active_live_progress = bool(" in source
    assert "current_task_pct < COMPLETION_THRESHOLD" in source
    assert "vendor_pct < COMPLETION_THRESHOLD" in source
    assert "not active_live_progress" in source
    assert 'progress_source = "active_live_recovery"' in source
