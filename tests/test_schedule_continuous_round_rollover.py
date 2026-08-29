"""Regression guards for safe 24-hour Navimower Schedule round rollover."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
ROUND = COMPONENT / "schedule_round_semantics.py"
RUNTIME = COMPONENT / "runtime.py"


def _function_source(name: str) -> str:
    source = ROUND.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"Function {name} not found")


def test_runtime_installs_continuous_round_semantics() -> None:
    source = RUNTIME.read_text(encoding="utf-8")
    assert "from .schedule_round_semantics import install_schedule_round_semantics" in source
    assert "install_schedule_pause_semantics()" in source
    assert "install_schedule_round_semantics()" in source
    assert source.index("install_schedule_pause_semantics()") < source.index(
        "install_schedule_round_semantics()"
    )


def test_round_completion_detects_custom_and_automatic_modes() -> None:
    source = _function_source("_continuous_round_complete")
    assert "SCHEDULE_MODE_CONTINUOUS" in source
    assert "SCHEDULE_ORDER_CUSTOM" in source
    assert 'get("completed_queue_slots")' in source
    assert 'get("completed_zone_ids_in_window")' in source
    assert "eligible_ids.issubset(completed_ids)" in source


def test_only_final_completion_arms_cross_round_deferral() -> None:
    source = _function_source("_confirm_active_completion")
    assert "_ORIGINAL_CONFIRM_ACTIVE_COMPLETION" in source
    assert "if completed and _continuous_round_complete(self):" in source
    assert "self._defer_continuous_round_handoff = True" in source


def test_cross_round_start_waits_for_normal_idle_state() -> None:
    source = _function_source("_async_send_mow")
    assert 'source == "navimower_schedule_next_zone"' in source
    assert "getattr(self, \"_defer_continuous_round_handoff\", False)" in source
    assert "activity in {ACTIVITY_MOWING, ACTIVITY_RETURNING}" in source
    assert "_ORIGINAL_ASYNC_SEND_MOW" in source


def test_within_round_direct_handoff_is_not_disabled_globally() -> None:
    source = _function_source("_async_send_mow")
    # The wrapper may defer only when the completion wrapper explicitly armed
    # the transient round-boundary flag. Ordinary zone-to-zone handoff still
    # reaches the original scheduler command path unchanged.
    assert "defer_round_handoff" in source
    assert "if defer_round_handoff and activity" in source
    assert "await _ORIGINAL_ASYNC_SEND_MOW(" in source
