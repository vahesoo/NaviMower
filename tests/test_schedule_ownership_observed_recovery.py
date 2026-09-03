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


def test_generic_observed_same_start_can_match_confirmed_scheduler_ownership() -> None:
    helper = _function_source("_generic_observed_matches_owned_dispatch")
    assert 'runtime.get("owned_zone_id")' in helper
    assert 'runtime.get("ownership_source")' in helper
    assert 'startswith("navimower_schedule")' in helper
    assert '_GENERIC_OBSERVED_TRIGGER' in helper
    assert '_dedupe_ids(task.get("zone_ids")) != [zone_id]' in helper
    assert 'runtime.get("owned_dispatch_started_at")' in helper
    assert 'task.get("started_at")' in helper
    assert '0.0 <= delta <= _GENERIC_OBSERVED_MATCH_SECONDS' in helper

    conflict = _function_source("_live_task_conflicts")
    assert 'target_ids != [zone_id]' in conflict
    assert 'not trigger.startswith("navimower_schedule")' in conflict
    assert '_generic_observed_matches_owned_dispatch(controller, task, zone_id)' in conflict
    assert conflict.index('_generic_observed_matches_owned_dispatch') < conflict.rindex('return True')


def test_later_or_explicit_external_task_still_fails_closed() -> None:
    helper = _function_source("_generic_observed_matches_owned_dispatch")
    assert 'str(task.get("trigger") or "") != _GENERIC_OBSERVED_TRIGGER' in helper
    assert 'str(task.get("origin") or "") not in {"", "observed"}' in helper
    assert '_GENERIC_OBSERVED_MATCH_SECONDS = 180.0' in SOURCE

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

    recovery = _function_source("_recover_unconfirmed_same_zone_charging_task")
    assert 'zone_id not in controller._selected_zone_ids' in recovery
    assert 'controller._eligible_zones()' in recovery
    assert 'controller._vendor_mowing(data)' in recovery
    assert 'not controller._vendor_charging(data)' in recovery
    assert 'getattr(center, "interrupted_reason", None) != "charging"' in recovery
    assert 'str(task.get("trigger") or "") != _GENERIC_OBSERVED_TRIGGER' in recovery
    assert '_dedupe_ids(task.get("zone_ids")) != [zone_id]' in recovery
    assert 'task.get("charging_paused_at")' in recovery
    assert 'runtime.get("last_command_at")' in recovery
    assert 'failed_at < paused_at' in recovery
    assert 'not 0.0 < progress < 100.0' in recovery


def test_recovery_restores_retained_low_battery_ownership_without_new_start() -> None:
    recovery = _function_source("_recover_unconfirmed_same_zone_charging_task")
    assert 'pause_semantics._matching_custom_queue_slot(controller, zone_id)' in recovery
    assert 'runtime["active_zone_id"] = zone_id' in recovery
    assert 'runtime["resume_pending"] = True' in recovery
    assert 'runtime["interrupted_reason"] = "low_battery"' in recovery
    assert 'runtime["interrupted_zone_id"] = zone_id' in recovery
    assert 'runtime["suspended_reason"] = None' in recovery
    assert 'runtime["owned_zone_id"] = zone_id' in recovery
    assert 'runtime["ownership_source"] = "navimower_schedule_recovered_same_zone_charging"' in recovery
    assert 'runtime["last_ownership_result"] = "recovered_unconfirmed_same_zone_charging"' in recovery
    assert 'runtime["last_error"] = None' in recovery
    assert '_async_send_mow' not in recovery
    assert 'async_resume_task' not in recovery
    assert 'mow_zones' not in recovery


def test_recovery_runs_before_normal_ownership_and_existing_resume_guard_remains() -> None:
    evaluate = _function_source("_evaluate_locked")
    assert 'await _recover_unconfirmed_same_zone_charging_task(self)' in evaluate
    assert evaluate.index('_recover_unconfirmed_same_zone_charging_task') < evaluate.index('_ownership_proven')

    resume = _function_source("_continue_interrupted_task")
    assert 'not _ownership_proven(self, zone_id)' in resume
    assert 'resume_refused_unverified_task' in resume
