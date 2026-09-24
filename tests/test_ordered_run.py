"""Tests for retained ordered-run continuation semantics."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "custom_components" / "navimower" / "ordered_run.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("navimower_ordered_run_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reset_false_inherits_already_complete_zones() -> None:
    ordered = _module()
    run = ordered.start_last_ordered_run(
        zone_ids=[92, 91, 5],
        zone_states=[
            {"id": 92, "coverage_pct": 100.0},
            {"id": 91, "coverage_pct": 63.0},
            {"id": 5, "coverage_pct": 0.0},
        ],
        reset=False,
        source="navimower.mow",
        started_at="2026-09-24T05:00:00+00:00",
        model="X390",
    )
    assert run["zone_ids"] == [92, 91, 5]
    assert run["completed_zone_ids"] == [92]
    assert run["remaining_zone_ids"] == [91, 5]
    assert run["resumable"] is True


def test_reset_true_does_not_inherit_stale_pre_reset_100() -> None:
    ordered = _module()
    run = ordered.start_last_ordered_run(
        zone_ids=[92, 91, 5],
        zone_states=[{"id": 92, "coverage_pct": 100.0}],
        reset=True,
        source="navimower.mow",
        started_at="2026-09-24T05:00:00+00:00",
    )
    assert run["completed_zone_ids"] == []
    assert run["remaining_zone_ids"] == [92, 91, 5]


def test_only_confirmed_completion_after_run_start_is_removed() -> None:
    ordered = _module()
    run = ordered.start_last_ordered_run(
        zone_ids=[92, 91, 5],
        zone_states=[],
        reset=True,
        source="navimower.mow",
        started_at="2026-09-24T05:00:00+00:00",
    )
    updated = ordered.update_last_ordered_run(
        run,
        zone_states=[
            {
                "id": 92,
                "coverage_pct": 100.0,
                "last_completed_at": "2026-09-24T04:59:59+00:00",
            },
            {
                "id": 91,
                "coverage_pct": 100.0,
                "last_completed_at": "2026-09-24T05:20:00+00:00",
            },
            {
                "id": 5,
                "coverage_pct": 99.0,
                "last_completed_at": None,
            },
        ],
    )
    assert updated is not None
    assert updated["completed_zone_ids"] == [91]
    assert updated["remaining_zone_ids"] == [92, 5]


def test_completion_is_monotonic_and_original_order_is_preserved() -> None:
    ordered = _module()
    run = ordered.start_last_ordered_run(
        zone_ids=[92, 91, 5],
        zone_states=[],
        reset=True,
        source="navimower.mow",
        started_at="2026-09-24T05:00:00+00:00",
    )
    run = ordered.update_last_ordered_run(
        run,
        zone_states=[
            {"id": 92, "last_completed_at": "2026-09-24T05:10:00+00:00"},
            {"id": 5, "last_completed_at": "2026-09-24T05:30:00+00:00"},
        ],
    )
    assert run is not None
    assert run["completed_zone_ids"] == [92, 5]
    assert run["remaining_zone_ids"] == [91]

    # A later stale snapshot must not resurrect a completed zone.
    run = ordered.update_last_ordered_run(run, zone_states=[])
    assert run is not None
    assert run["completed_zone_ids"] == [92, 5]
    assert run["remaining_zone_ids"] == [91]


def test_continue_records_reduced_list_without_replacing_original_run() -> None:
    ordered = _module()
    run = ordered.start_last_ordered_run(
        zone_ids=[92, 91, 5],
        zone_states=[{"id": 92, "coverage_pct": 100.0}],
        reset=False,
        source="navimower.mow",
        started_at="2026-09-24T05:00:00+00:00",
    )
    continued = ordered.record_ordered_run_continue(
        run,
        zone_ids=[91, 5],
        at="2026-09-24T06:00:00+00:00",
    )
    assert continued is not None
    assert continued["zone_ids"] == [92, 91, 5]
    assert continued["remaining_zone_ids"] == [91, 5]
    assert continued["last_continue_zone_ids"] == [91, 5]
    assert continued["continue_count"] == 1


def test_new_non_continuation_mow_supersedes_old_run() -> None:
    ordered = _module()
    run = ordered.start_last_ordered_run(
        zone_ids=[92, 91, 5],
        zone_states=[],
        reset=True,
        source="navimower.mow",
        started_at="2026-09-24T05:00:00+00:00",
    )
    superseded = ordered.supersede_last_ordered_run(
        run,
        source="navimower_schedule",
        at="2026-09-24T06:00:00+00:00",
    )
    assert superseded is not None
    assert superseded["resumable"] is False
    assert superseded["superseded_by"] == "navimower_schedule"
    assert superseded["remaining_zone_ids"] == [92, 91, 5]
