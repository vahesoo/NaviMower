"""Regression guards for managed-schedule queue handoff in beta45."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEDULE = ROOT / "custom_components" / "navimower" / "navimower_schedule.py"


def _method_source(name: str) -> str:
    source = SCHEDULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"Method {name} not found")


def test_custom_queue_slot_survives_command_confirmation_and_completion() -> None:
    send = _method_source("_async_send_mow")
    confirm = _method_source("_confirm_pending")
    complete = _method_source("_confirm_active_completion")

    assert '"queue_slot": queue_slot' in send
    assert 'self._runtime["active_queue_slot"] = pending.get("queue_slot")' in confirm
    assert 'self._runtime["completed_queue_slots"] = sorted(slots)' in complete


def test_completed_zone_can_handoff_while_vendor_is_returning() -> None:
    evaluate = _method_source("_evaluate_locked")

    assert "direct_handoff = (" in evaluate
    assert "completed_now" in evaluate
    assert "activity in {ACTIVITY_MOWING, ACTIVITY_RETURNING}" in evaluate
    assert "and not self._charging_interruption_confirmed()" in evaluate
    assert (
        "activity not in {ACTIVITY_DOCKED, ACTIVITY_PAUSED} and not direct_handoff"
        in evaluate
    )


def test_returning_without_completion_is_not_a_general_start_state() -> None:
    evaluate = _method_source("_evaluate_locked")

    # RETURNING is intentionally allowed only inside the completed_now handoff
    # expression. It must never be added to the ordinary idle start-state set.
    assert "activity not in {ACTIVITY_DOCKED, ACTIVITY_PAUSED} and not direct_handoff" in evaluate
    assert "{ACTIVITY_DOCKED, ACTIVITY_PAUSED, ACTIVITY_RETURNING}" not in evaluate
