"""Regression guards for managed-schedule ownership attribution and recovery."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNERSHIP = ROOT / "custom_components" / "navimower" / "schedule_ownership_semantics.py"
SOURCE = OWNERSHIP.read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(SOURCE, node)
            assert segment is not None
            return segment
    raise AssertionError(f"Function {name} not found")


def test_ownership_semantics_stay_syntax_valid() -> None:
    ast.parse(SOURCE)


def test_same_retained_task_can_survive_neutral_observation_or_manual_resume() -> None:
    helper = _function_source("_retained_task_matches_owned_dispatch")
    assert 'runtime.get("owned_zone_id")' in helper
    assert 'runtime.get("ownership_source")' in helper
    assert 'startswith("navimower_schedule")' in helper
    assert '_GENERIC_OBSERVED_TRIGGER' in helper
    assert '_MANUAL_RESUME_TRIGGER' in helper
    assert '_dedupe_ids(task.get("zone_ids")) != [zone_id]' in helper
    assert 'runtime.get("owned_dispatch_started_at")' in helper
    assert 'task.get("started_at")' in helper
    assert '0.0 <= delta <= _RETAINED_MATCH_SECONDS' in helper

    conflict = _function_source("_live_task_conflicts")
    assert 'target_ids != [zone_id]' in conflict
    assert 'not trigger.startswith("navimower_schedule")' in conflict
    assert '_retained_task_matches_owned_dispatch(controller, task, zone_id)' in conflict
    assert conflict.index('_retained_task_matches_owned_dispatch') < conflict.rindex('return True')


def test_later_or_explicit_external_task_still_fails_closed() -> None:
    helper = _function_source("_retained_task_matches_owned_dispatch")
    assert 'trigger not in {_GENERIC_OBSERVED_TRIGGER, _MANUAL_RESUME_TRIGGER}' in helper
    assert 'str(task.get("origin") or "") not in {"", "observed", "retained"}' in helper
    assert '_RETAINED_MATCH_SECONDS = 180.0' in SOURCE

    conflict = _function_source("_live_task_conflicts")
    external_block = conflict[conflict.index('if task_ids and not trigger.startswith') :]
    assert 'return True' in external_block


def test_beta24_false_ownership_failure_has_narrow_recovery_signature() -> None:
    signature = _function_source("_unconfirmed_retry_zone")
    assert '_RECOVERABLE_SUSPENSION' in signature
    assert 'active_task_rejected_unverified' in signature
    assert 'runtime.get("active_zone_id") is not None' in signature
    assert 'runtime.get("owned_zone_id") is not None' in signature
    assert 'isinstance(runtime.get("pending_command"), dict)' in signature
    assert 'prefix = "mow_start_unconfirmed:"' in signature

    allowed = _function_source("_recovery_zone_is_allowed")
    assert 'zone_id not in controller._selected_zone_ids' in allowed
    assert 'controller._eligible_zones()' in allowed

    recovery = _function_source("_recover_unconfirmed_same_zone_charging_task")
    assert 'controller._vendor_mowing(data)' in recovery
    assert 'data.get("docked") is not True' in recovery
    assert 'getattr(center, "interrupted_reason", None) != "charging"' in recovery
    assert 'str(task.get("trigger") or "") != _GENERIC_OBSERVED_TRIGGER' in recovery
    assert '_dedupe_ids(task.get("zone_ids")) != [zone_id]' in recovery
    assert 'task.get("charging_paused_at")' in recovery
    assert 'runtime.get("last_command_at")' in recovery
    assert 'failed_at < paused_at' in recovery
    assert 'not 0.0 < progress < 100.0' in recovery


def test_recovery_accepts_safe_docked_idle_after_charging_completed() -> None:
    recovery = _function_source("_recover_unconfirmed_same_zone_charging_task")
    assert '_vendor_charging' not in recovery
    assert 'data.get("docked") is not True' in recovery
    assert 'interrupted_reason", None) != "charging"' in recovery


def test_recovery_restores_retained_low_battery_ownership_without_new_start() -> None:
    recovery = _function_source("_recover_unconfirmed_same_zone_charging_task")
    restore = _function_source("_restore_retained_runtime")
    assert '_restore_retained_runtime(' in recovery
    assert 'interrupted_reason="low_battery"' in recovery
    assert 'ownership_source="navimower_schedule_recovered_same_zone_charging"' in recovery
    assert 'ownership_result="recovered_unconfirmed_same_zone_charging"' in recovery
    assert 'pause_semantics._matching_custom_queue_slot(controller, zone_id)' in restore
    assert 'runtime["active_zone_id"] = zone_id' in restore
    assert 'runtime["resume_pending"] = True' in restore
    assert 'runtime["interrupted_zone_id"] = zone_id' in restore
    assert 'runtime["suspended_reason"] = None' in restore
    assert 'runtime["owned_zone_id"] = zone_id' in restore
    assert 'runtime["last_error"] = None' in restore
    assert '_async_send_mow' not in recovery
    assert 'async_resume_task' not in recovery
    assert 'mow_zones' not in recovery


def test_manual_resume_then_night_pause_recovery_is_closed_window_only_and_same_task() -> None:
    recovery = _function_source("_recover_manual_resume_then_night_pause")
    assert '_unconfirmed_retry_zone(runtime)' in recovery
    assert '_recovery_zone_is_allowed(controller, zone_id)' in recovery
    assert 'controller._window_open_now()' in recovery
    assert 'controller._vendor_mowing(data)' in recovery
    assert 'data.get("docked") is not True' in recovery
    assert 'getattr(center, "interrupted_reason", None) != "night"' in recovery
    assert 'str(task.get("trigger") or "") != _MANUAL_RESUME_TRIGGER' in recovery
    assert '_dedupe_ids(task.get("zone_ids")) != [zone_id]' in recovery
    assert 'task.get("charging_paused_at")' in recovery
    assert 'task.get("night_paused_at")' in recovery
    assert 'runtime.get("last_command_at")' in recovery
    assert 'task_started <= charging_paused <= failed_at < night_paused' in recovery
    assert 'not 0.0 < progress < 100.0' in recovery
    assert 'interrupted_reason="window_closed"' in recovery
    assert 'ownership_source="navimower_schedule_recovered_manual_resume_night"' in recovery
    assert 'ownership_result="recovered_manual_resume_night_pause"' in recovery
    assert '_async_send_mow' not in recovery
    assert 'async_resume_task' not in recovery


def test_recoveries_run_before_normal_ownership_and_existing_resume_guard_remains() -> None:
    evaluate = _function_source("_evaluate_locked")
    assert 'await _recover_unconfirmed_same_zone_charging_task(self)' in evaluate
    assert 'await _recover_manual_resume_then_night_pause(self)' in evaluate
    assert evaluate.index('_recover_unconfirmed_same_zone_charging_task') < evaluate.index('_ownership_proven')
    assert evaluate.index('_recover_manual_resume_then_night_pause') < evaluate.index('_ownership_proven')

    resume = _function_source("_continue_interrupted_task")
    assert 'not _ownership_proven(self, zone_id)' in resume
    assert 'resume_refused_unverified_task' in resume
