"""Regression coverage for Navimower 0.4.3-beta56 current-cycle rendering."""
from __future__ import annotations

from custom_components.navimower.current_cycle_render import (
    build_current_cycle_render_source,
)


def _point(stamp: int, x: float, y: float, zone_id: int) -> list:
    # timestamp, x, y, heading, activity, mqtt state, mqtt action, zone_id
    return [stamp, x, y, 0.0, "mowing", 2, 1, zone_id]


def _session(
    session_id: str,
    start: int,
    points: list[list],
    *,
    active: bool = False,
    completed=False,
    reason: str | None = None,
    reset_zones: list[int] | None = None,
) -> dict:
    return {
        "id": session_id,
        "started_at_ms": start,
        "ended_at_ms": None if active else points[-1][0],
        "active": active,
        "completed": completed,
        "completion_reason": reason,
        "cycle_reset_zone_ids": list(reset_zones or []),
        "segment_starts_ms": [start],
        "zone_ids": sorted({int(point[7]) for point in points}),
        "points": points,
        "final_progress": {},
    }


def _zone_points(source: dict, zone_id: int) -> list[list]:
    return [point for point in source["points"] if int(point[7]) == zone_id]


def test_new_cycle_replaces_only_reset_zone() -> None:
    sessions = [
        _session(
            "old",
            1000,
            [
                _point(1000, 0.0, 0.0, 36),
                _point(1100, 1.0, 0.0, 36),
                _point(1200, 10.0, 0.0, 37),
                _point(1300, 11.0, 0.0, 37),
            ],
            completed=True,
        ),
        _session(
            "new",
            2000,
            [_point(2000, 2.0, 0.0, 36), _point(2100, 3.0, 0.0, 36)],
            completed=False,
            reset_zones=[36],
        ),
    ]

    source = build_current_cycle_render_source(sessions, [])

    assert [(point[1], point[2]) for point in _zone_points(source, 36)] == [
        (2.0, 0.0),
        (3.0, 0.0),
    ]
    assert [(point[1], point[2]) for point in _zone_points(source, 37)] == [
        (10.0, 0.0),
        (11.0, 0.0),
    ]


def test_continuation_fragments_accumulate_without_reset() -> None:
    sessions = [
        _session(
            "first",
            1000,
            [_point(1000, 0.0, 0.0, 36), _point(1100, 1.0, 0.0, 36)],
            completed=False,
        ),
        _session(
            "continued",
            2000,
            [_point(2000, 2.0, 0.0, 36), _point(2100, 3.0, 0.0, 36)],
            completed=False,
        ),
    ]

    source = build_current_cycle_render_source(sessions, [])

    assert [(point[1], point[2]) for point in _zone_points(source, 36)] == [
        (0.0, 0.0),
        (1.0, 0.0),
        (2.0, 0.0),
        (3.0, 0.0),
    ]
    assert len(source["segment_starts_ms"]) == 2


def test_active_reset_clears_old_completed_zone_without_archiving_live_points() -> None:
    sessions = [
        _session(
            "old",
            1000,
            [_point(1000, 0.0, 0.0, 36), _point(1100, 1.0, 0.0, 36)],
            completed=True,
        ),
        _session(
            "active-new-cycle",
            2000,
            [_point(2000, 2.0, 0.0, 36), _point(2100, 3.0, 0.0, 36)],
            active=True,
            reset_zones=[36],
        ),
    ]

    source = build_current_cycle_render_source(sessions, [])

    assert _zone_points(source, 36) == []
    assert source["current_cycle_zones"] == []


def test_in_session_cycle_boundary_drops_points_before_boundary() -> None:
    session = _session(
        "one-session",
        1000,
        [
            _point(1000, 0.0, 0.0, 36),
            _point(1100, 1.0, 0.0, 36),
            _point(2000, 2.0, 0.0, 36),
            _point(2100, 3.0, 0.0, 36),
        ],
        completed=False,
    )
    session["zone_cycle_boundaries"] = [{"zone_id": 36, "at_ms": 2000}]

    source = build_current_cycle_render_source([session], [])

    assert [(point[1], point[2]) for point in _zone_points(source, 36)] == [
        (2.0, 0.0),
        (3.0, 0.0),
    ]
