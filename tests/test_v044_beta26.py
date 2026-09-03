"""Release-contract guards for Navimower 0.4.4-beta26."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
MANIFEST = COMPONENT / "manifest.json"
OWNERSHIP = COMPONENT / "schedule_ownership_semantics.py"
NOTES = ROOT / ".github" / "release-notes" / "0.4.4-beta26.md"
SOURCE = OWNERSHIP.read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(SOURCE, node)
            assert segment is not None
            return segment
    raise AssertionError(f"Function {name} not found")


def test_beta26_version_and_release_notes() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.4-beta26"
    notes = NOTES.read_text(encoding="utf-8")
    for phrase in (
        "navimower.resume",
        "manual Navimower Resume",
        "paused for night",
        "window_closed",
        "without sending any mower command",
        "reset=true",
        "recovered_manual_resume_night_pause",
    ):
        assert phrase in notes


def test_beta26_manual_resume_preserves_only_same_confirmed_retained_task() -> None:
    helper = _function_source("_retained_task_matches_owned_dispatch")
    assert '_MANUAL_RESUME_TRIGGER = "navimower.resume"' in SOURCE
    assert 'runtime.get("owned_zone_id")' in helper
    assert 'startswith("navimower_schedule")' in helper
    assert '_dedupe_ids(task.get("zone_ids")) != [zone_id]' in helper
    assert 'runtime.get("owned_dispatch_started_at")' in helper
    assert 'task.get("started_at")' in helper
    assert '0.0 <= delta <= _RETAINED_MATCH_SECONDS' in helper

    conflict = _function_source("_live_task_conflicts")
    assert '_retained_task_matches_owned_dispatch(controller, task, zone_id)' in conflict
    assert 'return True' in conflict


def test_beta26_recovers_exact_manual_resume_then_night_pause_chain_only_when_closed() -> None:
    recovery = _function_source("_recover_manual_resume_then_night_pause")
    assert '_unconfirmed_retry_zone(runtime)' in recovery
    assert 'controller._window_open_now()' in recovery
    assert 'data.get("docked") is not True' in recovery
    assert 'interrupted_reason", None) != "night"' in recovery
    assert 'str(task.get("trigger") or "") != _MANUAL_RESUME_TRIGGER' in recovery
    assert '_dedupe_ids(task.get("zone_ids")) != [zone_id]' in recovery
    assert 'task.get("charging_paused_at")' in recovery
    assert 'task.get("night_paused_at")' in recovery
    assert 'task_started <= charging_paused <= failed_at < night_paused' in recovery
    assert 'interrupted_reason="window_closed"' in recovery
    assert 'ownership_result="recovered_manual_resume_night_pause"' in recovery
    assert '_async_send_mow' not in recovery
    assert 'async_resume_task' not in recovery
    assert 'mow_zones' not in recovery


def test_beta26_recovery_still_uses_existing_strict_resume_guard() -> None:
    evaluate = _function_source("_evaluate_locked")
    assert 'await _recover_manual_resume_then_night_pause(self)' in evaluate
    assert evaluate.index('_recover_manual_resume_then_night_pause') < evaluate.index('_ownership_proven')

    resume = _function_source("_continue_interrupted_task")
    assert 'not _ownership_proven(self, zone_id)' in resume
    assert 'resume_refused_unverified_task' in resume
