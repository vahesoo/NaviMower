"""Regression guards for Navimower 0.4.4-beta10 scheduler safety."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
OWNERSHIP = COMPONENT / "schedule_ownership_semantics.py"
ROUND = COMPONENT / "schedule_round_semantics.py"
RUNTIME = COMPONENT / "runtime.py"
MANIFEST = COMPONENT / "manifest.json"


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"Function {name} not found in {path.name}")


def test_beta10_version_and_runtime_install_order() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    version = str(manifest.get("version") or "")
    assert version.startswith("0.4.4-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 10
    source = RUNTIME.read_text(encoding="utf-8")
    assert "from .schedule_ownership_semantics import install_schedule_ownership_semantics" in source
    assert "install_schedule_pause_semantics()" in source
    assert "install_schedule_ownership_semantics()" in source
    assert "install_schedule_round_semantics()" in source
    assert source.index("install_schedule_pause_semantics()") < source.index(
        "install_schedule_ownership_semantics()"
    ) < source.index("install_schedule_round_semantics()")


def test_retained_task_requires_positive_scheduler_ownership() -> None:
    source = _function_source(OWNERSHIP, "_adopt_retained_task")
    assert "_ownership_proven(controller, active_zone_id)" in source
    assert "retained_task_rejected_unverified" in source
    assert "_scheduler_task_evidence" in source

    evidence = _function_source(OWNERSHIP, "_scheduler_task_evidence")
    assert 'trigger.startswith("navimower_schedule")' in evidence
    assert "task_ids == [zone_id]" in evidence


def test_live_multi_zone_or_external_task_conflicts_with_one_zone_ownership() -> None:
    source = _function_source(OWNERSHIP, "_live_task_conflicts")
    assert 'data.get("target_zone_ids")' in source
    assert "target_ids != [zone_id]" in source
    assert 'not trigger.startswith("navimower_schedule")' in source


def test_resume_refuses_unverified_vendor_retained_task() -> None:
    source = _function_source(OWNERSHIP, "_continue_interrupted_task")
    assert "not _ownership_proven(self, zone_id)" in source
    assert "resume_refused_unverified_task" in source
    assert "vendor Resume was refused" in source
    assert "_ORIGINAL_CONTINUE_INTERRUPTED_TASK" in source


def test_confirmed_scheduler_start_records_explicit_ownership() -> None:
    source = _function_source(OWNERSHIP, "_confirm_pending")
    assert 'pending.get("kind") != "mow"' in source
    assert 'source.startswith("navimower_schedule")' in source
    assert 'self._runtime["owned_zone_id"] = zone_id' in source
    assert 'self._runtime["ownership_source"] = source' in source


def test_window_mode_repeats_completed_rounds_but_keeps_window_boundary() -> None:
    source = _function_source(ROUND, "_evaluate_locked")
    assert "self._mode != SCHEDULE_MODE_WINDOW" in source
    assert "in_window, _ = self._window_state(dt_util.now())" in source
    assert "if not in_window" in source
    assert "not _continuous_round_complete(self)" in source
    assert 'self._runtime["completed_zone_ids_in_window"] = []' in source
    assert 'self._runtime["completed_queue_slots"] = []' in source
    assert 'self._runtime["round_index"]' in source


def test_new_round_waits_for_idle_when_previous_task_is_still_finishing() -> None:
    source = _function_source(ROUND, "_async_send_mow")
    assert "activity in {ACTIVITY_MOWING, ACTIVITY_RETURNING}" in source
    assert "round_" in source and "waiting_idle" in source
