"""Regression guards for positional Navimower Schedule custom queues."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
QUEUE = COMPONENT / "schedule_queue_semantics.py"
BOUNDARY = COMPONENT / "schedule_queue_boundary_semantics.py"
RUNTIME = COMPONENT / "runtime.py"


def test_custom_queue_round_snapshot_is_positional_and_duplicate_safe() -> None:
    source = QUEUE.read_text(encoding="utf-8")

    assert '"round_queue": []' in source
    assert '"started_queue_slots": []' in source
    assert "for slot, zone_id in enumerate(_execution_queue(self))" in source
    assert "slot in started" in source
    assert 'int(entry["zone_id"]) == zone_id' in source


def test_started_unfinished_slot_blocks_a_fresh_reset() -> None:
    source = QUEUE.read_text(encoding="utf-8")

    block = source[
        source.index("def _next_custom_entry"):
        source.index("def _matching_custom_queue_slot")
    ]
    assert "if (started - completed) & valid_slots:" in block
    assert "return None" in block
    assert '"queue_slot_ownership_unverified"' in source
    assert "a fresh reset was refused" in source


def test_queue_edits_are_staged_when_round_has_progress() -> None:
    source = QUEUE.read_text(encoding="utf-8")
    block = source[
        source.index("async def _async_set_custom_queue"):
        source.index("async def _confirm_pending")
    ]

    assert "progress = _round_has_progress(self)" in block
    assert 'if progress and not self._runtime.get("round_queue")' in block
    assert "if not progress:" in block
    assert '"queue_saved_for_next_round" if progress' in block


def test_confirmed_scheduler_start_marks_exact_slot_started() -> None:
    source = QUEUE.read_text(encoding="utf-8")
    block = source[
        source.index("async def _confirm_pending"):
        source.index("async def _evaluate_locked")
    ]

    assert 'pending.get("queue_slot")' in block
    assert 'self._runtime.get("active_queue_slot")' in block
    assert 'self._runtime["started_queue_slots"] = sorted(started)' in block


def test_staged_queue_applies_before_new_window_or_continuous_round_dispatch() -> None:
    source = BOUNDARY.read_text(encoding="utf-8")

    assert "def _prepare_new_window_snapshot" in source
    assert 'controller._runtime["round_queue"] = _configured_round_queue(controller)' in source
    assert "def _prepare_next_continuous_round" in source
    assert 'controller._runtime["completed_queue_slots"] = []' in source
    assert 'controller._runtime["round_index"]' in source


def test_queue_semantics_install_after_ownership_and_round_semantics() -> None:
    source = RUNTIME.read_text(encoding="utf-8")

    pause = source.index("install_schedule_pause_semantics()")
    ownership = source.index("install_schedule_ownership_semantics()")
    round_semantics = source.index("install_schedule_round_semantics()")
    queue = source.index("install_schedule_queue_semantics()")
    boundary = source.index("install_schedule_queue_boundary_semantics()")
    assert pause < ownership < round_semantics < queue < boundary
