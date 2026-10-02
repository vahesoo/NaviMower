"""Release contract for 0.5.0-beta8 Scheduler V2 and trail repair."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
RUNTIME = COMPONENT / "runtime.py"
V2 = COMPONENT / "schedule_v2_semantics.py"
STORE = COMPONENT / "vendor_trail_store.py"


def test_beta8_version_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta8"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta8.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Scheduler V2",
        "Dock probe",
        "Resume",
        "queue pointer",
        "gap guard v2",
        "0.4.0-beta7",
    ):
        assert marker in notes


def test_beta8_runtime_installs_only_scheduler_v2_policy_layer() -> None:
    runtime = RUNTIME.read_text(encoding="utf-8")
    assert "install_schedule_v2_semantics()" in runtime
    for legacy in (
        "install_schedule_pause_semantics()",
        "install_schedule_ownership_semantics()",
        "install_schedule_round_semantics()",
        "install_schedule_queue_semantics()",
        "install_schedule_queue_boundary_semantics()",
        "install_schedule_dispatch_weather_semantics()",
        "install_schedule_queue_recovery_semantics()",
    ):
        assert legacy not in runtime


def test_v2_is_window_and_queue_pointer_based_not_ownership_based() -> None:
    source = V2.read_text(encoding="utf-8")
    assert '"current_slot": None' in source
    assert '"unfinished": False' in source
    assert '"round_queue": []' in source
    assert "_complete_current_slot(controller)" in source
    assert "_start_round(controller, increment=True, reason=\"round_complete\")" in source
    assert "ownership_source" not in source
    assert "owned_zone_id" not in source
    assert "last_ownership_result" not in source


def test_v2_closed_window_and_resume_contract() -> None:
    source = V2.read_text(encoding="utf-8")
    assert 'reason="window_closed_probe"' in source
    assert "controller._vendor_mowing(data) or activity == ACTIVITY_PAUSED" in source
    assert "controller.coordinator.client.resume" in source
    assert "navimower_schedule_v2_continue" in source
    assert "reset=False" in source
    assert "resume_attempted_window_token" in source


def test_v2_completion_handoff_ignores_stale_previous_zone_mowing_snapshot() -> None:
    source = V2.read_text(encoding="utf-8")
    assert "completed_now = _complete_current_slot(controller)" in source
    assert "controller._vendor_mowing(data) and not completed_now" in source
    assert "No Dock boundary is required" in source


def test_gap_guard_v2_rebuilds_old_false_break_artifacts() -> None:
    source = STORE.read_text(encoding="utf-8")
    assert "FUTURE_VENDOR_GAP_MIN_SPLIT_M = 15.0" in source
    assert "FUTURE_VENDOR_GAP_MAX_SPLIT_M = 30.0" in source
    assert "FUTURE_VENDOR_GAP_MEDIAN_MULTIPLIER = 6.0" in source
    assert "FUTURE_VENDOR_GAP_GUARD_VERSION = 2" in source
    assert "migrating_guard" in source
    assert '"artifact": None if migrating_guard' in source
