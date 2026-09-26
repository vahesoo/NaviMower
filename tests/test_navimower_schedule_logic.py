from datetime import datetime, time, timezone
import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "navimower" / "schedule_logic.py"
spec = importlib.util.spec_from_file_location("navimower_schedule_logic", MODULE_PATH)
assert spec and spec.loader
logic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logic)
classify_schedule_mow_start = logic.classify_schedule_mow_start
completion_advanced = logic.completion_advanced
filter_schedule_zones = logic.filter_schedule_zones
select_oldest_zone = logic.select_oldest_zone
window_state = logic.window_state


def test_same_day_window():
    now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    assert window_state(now, time(10, 0), time(20, 0)) == (True, "2026-08-15")
    assert window_state(now, time(13, 0), time(20, 0)) == (False, None)


def test_cross_midnight_window_uses_start_date_token():
    early = datetime(2026, 8, 16, 1, 0, tzinfo=timezone.utc)
    assert window_state(early, time(20, 0), time(2, 0)) == (True, "2026-08-15")


def test_oldest_zone_and_guards():
    zones = [
        {"id": 1, "last_completed_at": "2026-08-14T10:00:00+00:00"},
        {"id": 2, "last_completed_at": "2026-08-13T10:00:00+00:00"},
        {"id": 3, "last_completed_at": None},
    ]
    assert select_oldest_zone(zones)["id"] == 2
    assert select_oldest_zone(zones, completed_in_window={2})["id"] == 1
    assert select_oldest_zone(zones, completed_in_window={2}, just_completed_zone_id=1) is None
    assert [row["id"] for row in filter_schedule_zones(zones, [1, 2, 3])] == [1, 2]


def test_scheduler_confirmed_completion_beats_stale_cloud_value():
    zones = [
        {"id": 1, "last_completed_at": "2026-08-10T10:00:00+00:00"},
        {"id": 2, "last_completed_at": "2026-08-11T10:00:00+00:00"},
    ]
    confirmed = {"1": "2026-08-15T10:00:00+00:00"}
    assert select_oldest_zone(zones, scheduler_completed_at=confirmed)["id"] == 2


def test_completion_must_be_newer_than_baseline_and_dispatch():
    baseline = "2026-08-14T10:00:00+00:00"
    dispatch = "2026-08-15T10:00:00+00:00"
    assert not completion_advanced(baseline, baseline, dispatch)
    assert not completion_advanced("2026-08-15T09:59:59+00:00", baseline, dispatch)
    assert completion_advanced("2026-08-15T10:30:00+00:00", baseline, dispatch)


def test_scheduler_start_rejects_fresh_wrong_zone_mqtt_evidence():
    result = classify_schedule_mow_start(
        37,
        vendor_mowing_now=True,
        vendor_mowing_at_send=False,
        data={
            "active_zone_progress_zone_id": 36,
            "active_zone_progress_source_age": 2.0,
            "mqtt_action_age": 2.0,
        },
        mqtt_location={
            "work_target_zone": 36,
            "mow_boundary": 36,
            "pose_time": 1790411130079,
        },
        sent_at="2026-09-26T08:24:47+00:00",
    )
    assert result["state"] == "zone_mismatch"
    assert result["strong_mismatch_zone_ids"] == [36]


def test_scheduler_start_confirms_matching_fresh_zone():
    result = classify_schedule_mow_start(
        37,
        vendor_mowing_now=True,
        vendor_mowing_at_send=False,
        data={
            "active_zone_progress_zone_id": 37,
            "active_zone_progress_source_age": 2.0,
            "mqtt_action_age": 2.0,
        },
        mqtt_location={
            "work_target_zone": 37,
            "mow_boundary": 37,
            "pose_time": 1790411130079,
        },
        sent_at="2026-09-26T08:24:47+00:00",
    )
    assert result["state"] == "confirmed"


def test_scheduler_start_keeps_legacy_transition_fallback_without_zone_evidence():
    result = classify_schedule_mow_start(
        37,
        vendor_mowing_now=True,
        vendor_mowing_at_send=False,
        data={},
        mqtt_location={},
        sent_at="2026-09-26T08:24:47+00:00",
    )
    assert result["state"] == "confirmed"
