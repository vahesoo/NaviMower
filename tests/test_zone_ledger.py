from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "custom_components" / "navimower" / "zone_ledger.py"

spec = importlib.util.spec_from_file_location("navimower_zone_ledger_test", MODULE_PATH)
assert spec and spec.loader
ledger = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ledger
spec.loader.exec_module(ledger)

NOW = 1_800_000_000_000
ZONE = {"id": 5, "name": "Plats 1", "area": 100.0, "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]}


def snapshot(
    pct: float | None,
    *,
    start: int | None = 100,
    end: int | None = None,
    age: float | None = 1.0,
    live: float | None = None,
    active_zone_id: int | None = 5,
    mowing_progress: float | None = None,
    session_area: float | None = None,
) -> dict:
    coverage = {"zones": []}
    if pct is not None:
        row = {
            "id": 5,
            "area": 100.0,
            "finished": pct,
            "pct": pct,
        }
        if start is not None:
            row["start_time"] = start
        if end is not None:
            row["end_time"] = end
        coverage["zones"].append(row)
    result = {
        "coverage": coverage,
        "coverage_source_age": age,
        "active_zone_progress_zone_id": active_zone_id,
        "active_zone_progress": live,
        "mowing_progress": mowing_progress,
        "mowing_progress_source": "mqtt_task_percentage",
        "session_area": session_area,
        "session_area_source": "mqtt_subtotal_area",
        "target_zone_ids": [5],
        "activity": "mowing",
    }
    return result


def reduce(
    state=None,
    *,
    pct: float | None,
    start: int | None = 100,
    end: int | None = None,
    age: float | None = 1.0,
    live: float | None = None,
    map_zone: dict | None = None,
    observed_at_ms: int = NOW,
    mowing_progress: float | None = None,
    session_area: float | None = None,
):
    return ledger.reduce_zone_ledger(
        state,
        snapshot=snapshot(
            pct,
            start=start,
            end=end,
            age=age,
            live=live,
            mowing_progress=mowing_progress,
            session_area=session_area,
        ),
        map_zones=[map_zone or ZONE],
        zone_details=[{"id": 5, "name": "Plats 1", "area_m2": 100.0}],
        zone_history={},
        active_session={"id": "legacy-session", "zone_ids": [5], "visited_zone_ids": [5]},
        observed_at_ms=observed_at_ms,
    )


@pytest.mark.parametrize(
    ("initial", "next_pct", "next_start", "age", "live", "expected_pct", "expected_source", "reset"),
    [
        (None, 0, 100, 1, None, 0.0, "vendor_coverage", False),
        (None, 37, 100, 1, None, 37.0, "vendor_coverage", False),
        (None, 100, 100, 1, None, 100.0, "vendor_coverage", False),
        (20, 21, 100, 1, None, 21.0, "vendor_coverage", False),
        (60, 55, 100, 1, None, 60.0, "vendor_coverage_monotonic_hold", False),
        (60, 61, 100, 1, None, 61.0, "vendor_coverage", False),
        (100, 99, 100, 1, None, 100.0, "vendor_coverage_monotonic_hold", False),
        (100, 100, 100, 1, None, 100.0, "vendor_coverage", False),
        (80, 10, 200, 1, None, 10.0, "vendor_coverage", True),
        (80, 10, 100, 1, 80, 80.0, "vendor_coverage_monotonic_hold", False),
        (80, 10, 100, 120, None, 80.0, "vendor_coverage", False),
        (25, 24, 100, 1, None, 25.0, "vendor_coverage_monotonic_hold", False),
        (25, 26, 100, 1, None, 26.0, "vendor_coverage", False),
        (95, 94, 100, 1, None, 95.0, "vendor_coverage_monotonic_hold", False),
        (99, 100, 100, 1, None, 100.0, "vendor_coverage", False),
    ],
)
def test_zone_ledger_transition_table(
    initial,
    next_pct,
    next_start,
    age,
    live,
    expected_pct,
    expected_source,
    reset,
) -> None:
    state = None
    if initial is not None:
        state, *_ = reduce(pct=initial, start=100)
    state, rows, _totals, _task, events = reduce(
        state,
        pct=next_pct,
        start=next_start,
        age=age,
        live=live,
        observed_at_ms=NOW + 10_000,
    )
    row = rows[0]
    assert row["coverage_pct"] == expected_pct
    if age is not None and age > ledger.COVERAGE_FRESH_MAX_AGE_S:
        assert row["stale"] is True
    else:
        assert row["progress_source"] == expected_source
    assert any(event["type"] == "zone_cycle_reset" for event in events) is reset


def test_hard_drop_requires_two_confirmations_without_strong_reset_signal() -> None:
    state, *_ = reduce(pct=80, start=100)
    state, rows, _totals, _task, first_events = reduce(
        state, pct=10, start=100, observed_at_ms=NOW + 10_000
    )
    assert rows[0]["coverage_pct"] == 80.0
    assert not any(event["type"] == "zone_cycle_reset" for event in first_events)

    state, rows, _totals, _task, second_events = reduce(
        state, pct=10, start=100, observed_at_ms=NOW + 20_000
    )
    assert rows[0]["coverage_pct"] == 10.0
    assert any(
        event["type"] == "zone_cycle_reset"
        and event["reason"] == "confirmed_vendor_low_drop"
        for event in second_events
    )


def test_active_live_progress_blocks_false_low_drop_confirmation() -> None:
    state, *_ = reduce(pct=80, start=100)
    for offset in (10_000, 20_000, 30_000):
        state, rows, _totals, _task, events = reduce(
            state,
            pct=10,
            start=100,
            live=75,
            observed_at_ms=NOW + offset,
        )
        assert rows[0]["coverage_pct"] == 80.0
        assert not any(event["type"] == "zone_cycle_reset" for event in events)


def test_geometry_change_allows_immediate_new_cycle_drop() -> None:
    state, *_ = reduce(pct=80, start=100)
    changed = {
        **ZONE,
        "polygon": [[0, 0], [12, 0], [12, 10], [0, 10]],
        "area": 120.0,
    }
    state, rows, _totals, _task, events = reduce(
        state,
        pct=0,
        start=100,
        map_zone=changed,
        observed_at_ms=NOW + 10_000,
    )
    assert rows[0]["coverage_pct"] == 0.0
    assert rows[0]["area_m2"] == 120.0
    assert any(
        event["type"] == "zone_cycle_reset" and event["reason"] == "zone_geometry_changed"
        for event in events
    )


def test_missing_or_stale_vendor_data_holds_last_good_zone_state() -> None:
    state, *_ = reduce(pct=73, start=100)
    state, rows, totals, _task, events = reduce(
        state,
        pct=None,
        age=120,
        observed_at_ms=NOW + 120_000,
    )
    assert rows[0]["coverage_pct"] == 73.0
    assert rows[0]["mowed_area_m2"] == 73.0
    assert rows[0]["stale"] is True
    assert totals["map_coverage_pct"] == 73.0
    assert events == []


def test_completion_is_recorded_only_on_transition_to_100() -> None:
    state, *_ = reduce(pct=99, start=100)
    state, rows, _totals, _task, events = reduce(
        state,
        pct=100,
        start=100,
        end=1_800_000_010,
        observed_at_ms=NOW + 20_000,
    )
    completion_events = [event for event in events if event["type"] == "zone_completed"]
    assert len(completion_events) == 1
    first_completed_at = rows[0]["last_completed_at"]

    state, rows, _totals, _task, events = reduce(
        state,
        pct=100,
        start=100,
        end=1_800_000_010,
        observed_at_ms=NOW + 30_000,
    )
    assert not any(event["type"] == "zone_completed" for event in events)
    assert rows[0]["last_completed_at"] == first_completed_at


def test_explicit_reset_zeroes_only_selected_zone_and_waits_for_vendor_ack() -> None:
    state, *_ = reduce(pct=100, start=100)
    state, events = ledger.mark_explicit_reset(
        state,
        [5],
        observed_at_ms=NOW + 10_000,
        reason="service_reset",
    )
    row = ledger.zone_rows_by_id(state)[5]
    assert row["progress_pct"] == 0.0
    assert row["pending_vendor_cycle"] is True
    assert events[0]["source"] == "explicit_command"

    state, rows, _totals, _task, events = reduce(
        state,
        pct=100,
        start=100,
        observed_at_ms=NOW + 20_000,
    )
    assert rows[0]["coverage_pct"] == 0.0
    assert rows[0]["pending_vendor_cycle"] is True
    assert events == []

    state, rows, _totals, _task, events = reduce(
        state,
        pct=3,
        start=200,
        observed_at_ms=NOW + 30_000,
    )
    assert rows[0]["coverage_pct"] == 3.0
    assert rows[0]["pending_vendor_cycle"] is False
    assert any(event["type"] == "zone_cycle_reset" for event in events)


def test_map_totals_are_derived_from_retained_zone_rows() -> None:
    state = None
    state, rows, totals, _task, _events = ledger.reduce_zone_ledger(
        state,
        snapshot={
            "coverage": {
                "zones": [
                    {"id": 5, "area": 100.0, "finished": 100.0, "pct": 100, "start_time": 100},
                    {"id": 6, "area": 300.0, "finished": 150.0, "pct": 50, "start_time": 100},
                ]
            },
            "coverage_source_age": 1,
            "target_zone_ids": [5, 6],
            "activity": "mowing",
        },
        map_zones=[
            ZONE,
            {"id": 6, "name": "Plats 2", "area": 300.0, "polygon": [[20, 0], [50, 0], [50, 10], [20, 10]]},
        ],
        zone_details=[],
        zone_history={},
        active_session={"zone_ids": [5, 6], "visited_zone_ids": [5, 6]},
        observed_at_ms=NOW,
    )
    assert {row["id"]: row["coverage_pct"] for row in rows} == {5: 100.0, 6: 50.0}
    assert totals["map_area_m2"] == 400.0
    assert totals["map_mowed_area_m2"] == 250.0
    assert totals["map_coverage_pct"] == 62.5


def test_task_state_uses_direct_task_counters_without_overwriting_zone_coverage() -> None:
    _state, rows, totals, task, _events = reduce(
        pct=56,
        mowing_progress=48,
        session_area=48,
    )
    assert rows[0]["coverage_pct"] == 56.0
    assert task["progress_pct"] == 48.0
    assert task["mowed_area_m2"] == 48.0
    assert task["progress_source"] == "mqtt_task_percentage"
    assert totals["task_progress_pct"] == 48.0


def test_geometry_signature_is_rotation_and_direction_invariant() -> None:
    first = ledger.zone_geometry_signature(ZONE)
    rotated = {**ZONE, "polygon": [[10, 10], [0, 10], [0, 0], [10, 0]]}
    reversed_polygon = {**ZONE, "polygon": [[0, 0], [0, 10], [10, 10], [10, 0]]}
    assert ledger.zone_geometry_signature(rotated) == first
    assert ledger.zone_geometry_signature(reversed_polygon) == first
