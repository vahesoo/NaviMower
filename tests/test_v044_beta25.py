"""Release-contract guards for Navimower 0.4.4-beta25."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
MANIFEST = COMPONENT / "manifest.json"
OWNERSHIP = COMPONENT / "schedule_ownership_semantics.py"
NOTES = ROOT / ".github" / "release-notes" / "0.4.4-beta25.md"


def _function_source(name: str) -> str:
    source = OWNERSHIP.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"Function {name} not found")


def test_beta25_version_and_release_notes() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.4-beta25"
    notes = NOTES.read_text(encoding="utf-8")
    for phrase in (
        "observed_without_local_command",
        "active_task_rejected_unverified",
        "mow_start_unconfirmed:<zone>",
        "low-battery",
        "without sending any mower command",
        "Native Navimow Schedule",
        "Resume control",
    ):
        assert phrase in notes


def test_beta25_generic_observation_is_bounded_to_same_scheduler_dispatch() -> None:
    source = _function_source("_generic_observed_matches_owned_dispatch")
    assert 'owned_zone_id' in source
    assert 'ownership_source' in source
    assert 'startswith("navimower_schedule")' in source
    assert '_GENERIC_OBSERVED_TRIGGER' in source
    assert '_dedupe_ids(task.get("zone_ids")) != [zone_id]' in source
    assert 'owned_dispatch_started_at' in source
    assert '0.0 <= delta <= _GENERIC_OBSERVED_MATCH_SECONDS' in source


def test_beta25_field_state_recovery_never_starts_a_new_cycle() -> None:
    source = _function_source("_recover_unconfirmed_same_zone_charging_task")
    assert 'active_task_rejected_unverified' in OWNERSHIP.read_text(encoding="utf-8")
    assert 'mow_start_unconfirmed:' in OWNERSHIP.read_text(encoding="utf-8")
    assert 'interrupted_reason", None) != "charging"' in source
    assert 'runtime["resume_pending"] = True' in source
    assert 'runtime["interrupted_reason"] = "low_battery"' in source
    assert 'runtime["owned_zone_id"] = zone_id' in source
    assert 'runtime["suspended_reason"] = None' in source
    assert 'recovered_unconfirmed_same_zone_charging' in source
    assert '_async_send_mow' not in source
    assert 'async_resume_task' not in source
    assert 'start_new_mowing_cycle' not in source


def test_beta25_keeps_strict_resume_refusal_for_unverified_tasks() -> None:
    source = _function_source("_continue_interrupted_task")
    assert 'not _ownership_proven(self, zone_id)' in source
    assert 'resume_refused_unverified_task' in source
    assert 'vendor Resume was refused' in source
