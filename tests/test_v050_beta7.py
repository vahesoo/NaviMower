"""Release contract for 0.5.0-beta7 scheduler round recovery."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
QUEUE = COMPONENT / "schedule_queue_semantics.py"
ROUND = COMPONENT / "schedule_round_semantics.py"


def test_beta7_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta7"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta7.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Scheduler round recovery",
        "active_queue_slot",
        "started-but-not-completed",
        "Time window",
    ):
        assert marker in notes


def test_retained_slot_seed_preserves_positional_identity() -> None:
    source = QUEUE.read_text(encoding="utf-8")
    start = source.index("def _seed_started_slot_from_runtime")
    end = source.index("def _recover_completed_started_slot", start)
    block = source[start:end]
    assert '_matching_custom_queue_slot(controller, zone_id)' in block
    assert 'controller._runtime["active_queue_slot"] = slot' in block
    assert 'controller._runtime["started_queue_slots"] = sorted(started)' in block


def test_beta6_completion_recovery_is_narrow_and_command_free() -> None:
    source = QUEUE.read_text(encoding="utf-8")
    start = source.index("def _recover_completed_started_slot")
    end = source.index("def _round_has_progress", start)
    block = source[start:end]
    assert '"owned_zone_completed"' in block
    assert 'f"zone_completed:{zone_id}"' in block
    assert 'scheduler_completed_at' in block
    assert "if len(matches) != 1:" in block
    assert 'runtime["completed_queue_slots"] = sorted(completed)' in block
    assert "_async_send_mow" not in block


def test_time_window_round_repeat_contract_remains_enabled() -> None:
    source = ROUND.read_text(encoding="utf-8")
    assert "Time-window mode now does the same while the window remains open" in source
    assert "self._mode != SCHEDULE_MODE_WINDOW" in source
    assert 'self._runtime["round_index"]' in source
