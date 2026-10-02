"""Release contract for 0.5.0-beta9 Scheduler V2 practical completion."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
V2 = COMPONENT / "schedule_v2_semantics.py"


def test_beta9_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta9"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta9.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Practical completion",
        "95-99%",
        "Returning",
        "low battery",
        "Street2",
    ):
        assert marker in notes


def test_practical_completion_requires_terminal_non_cutting_state() -> None:
    source = V2.read_text(encoding="utf-8")
    start = source.index("def _practical_completion_evidence")
    end = source.index("def _build_round_queue", start)
    block = source[start:end]

    assert "activity not in {ACTIVITY_RETURNING, ACTIVITY_DOCKED}" in block
    assert "controller._vendor_mowing(data)" in block
    assert "_interruption_reason(controller, data)" in block
    assert "VENDOR_COMPLETION_PROGRESS_MIN" in block
    assert "progress >= 100.0" in block
    assert "source_age > 90.0" in block
    assert "vendor_start_time" in block


def test_practical_completion_blocks_known_interruptions() -> None:
    source = V2.read_text(encoding="utf-8")
    start = source.index("def _interruption_reason")
    end = source.index("def _practical_completion_evidence", start)
    block = source[start:end]

    for marker in (
        '"charging"',
        '"low_battery"',
        '"night"',
        '"rain"',
        '"vendor_task_delay"',
        '"manual_pause"',
        'data.get("error") is True',
        'data.get("problem_latched") is True',
        'return_battery_level',
    ):
        assert marker in block


def test_completion_advances_queue_for_strict_or_practical_evidence() -> None:
    source = V2.read_text(encoding="utf-8")
    start = source.index("def _complete_current_slot")
    end = source.index("async def _enforce_closed_window", start)
    block = source[start:end]

    assert "strict = _zone_completed_after(" in block
    assert "practical, practical_reason = _practical_completion_evidence(" in block
    assert "if not strict and not practical:" in block
    assert "zone_completed_practical" in block
    assert "_start_round(controller, increment=True, reason=\"round_complete\")" in block


def test_returning_state_never_sends_resume_or_continue() -> None:
    source = V2.read_text(encoding="utf-8")
    start = source.index('    if runtime.get("unfinished"):', source.index("async def _evaluate_locked"))
    end = source.index("    # A fresh queue slot", start)
    block = source[start:end]

    returning = block.index("if activity == ACTIVITY_RETURNING:")
    charging = block.index("if not _charging_ready(controller, data):")
    resume = block.index("_send_resume(controller, zone_id)")
    continue_send = block.index("_send_continue(controller, slot, zone_id)")
    assert returning < charging < resume < continue_send
    assert "return" in block[returning:charging]
