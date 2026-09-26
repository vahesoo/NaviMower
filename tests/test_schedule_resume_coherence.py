"""Permanent regressions for managed Schedule / smart Resume coherence."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_managed_schedule_guards_public_resume_contract() -> None:
    task_resume = (COMPONENT / "task_resume.py").read_text(encoding="utf-8")
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    services = (COMPONENT / "services.py").read_text(encoding="utf-8")
    ast.parse(task_resume)
    ast.parse(coordinator)
    ast.parse(services)

    assert "def guard_managed_schedule_resume(" in task_resume
    assert '"reason"] = "managed_schedule_active"' in task_resume
    assert '"managed_schedule_controls_resume"' in task_resume
    assert 'snapshot["task_resume"] = guard_managed_schedule_resume(' in coordinator
    assert 'decision = guard_managed_schedule_resume(decision, schedule_state)' in services


def test_resume_refreshes_notification_task_to_observed_vendor_zone() -> None:
    source = (COMPONENT / "notification_center.py").read_text(encoding="utf-8")
    ast.parse(source)
    start = source.index("resume_trace = self._recent_resume_trace()")
    end = source.index("mow_trace = self._recent_mow_trace()", start)
    block = source[start:end]
    assert "observed_ids = self._observed_task_zone_ids(snapshot)" in block
    assert 'self._active_task["zone_ids"] = list(observed_ids)' in block
    assert 'self._active_task["zone_names"] = list(names)' in block


def test_ownership_accepts_smart_resume_as_retained_task_continuation() -> None:
    source = (COMPONENT / "schedule_ownership_semantics.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert '_MANUAL_RESUME_TRIGGERS = {' in source
    for trigger in (
        '"navimower.resume"',
        '"navimower.continue_task"',
        '"lawn_mower.start_mowing_paused"',
    ):
        assert trigger in source
