"""Regressions for app-like same-day per-zone trail retention."""
from __future__ import annotations

from datetime import date
import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
PACKAGE = "navimower_daily_trail_test"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules.setdefault(PACKAGE, package)
_load_module(f"{PACKAGE}.const", COMPONENT / "const.py")
zone_state = _load_module(f"{PACKAGE}.zone_state", COMPONENT / "zone_state.py")

LOCAL_DAY = date(2026, 8, 4)


def _point(stamp: int, x: float, zone_id: int) -> list[object]:
    return [stamp, x, 1.0, 0.0, "mowing", 4, -1, zone_id]


def _build(sessions: list[dict]) -> dict[int, dict]:
    payload = zone_state.build_daily_trails(
        sessions=sessions,
        map_zones=[],
        local_date=LOCAL_DAY,
        to_local_date=lambda _stamp: LOCAL_DAY,
        revision=1,
    )
    return {row["zone_id"]: row for row in payload["zones"]}


def test_active_charge_continuation_keeps_previous_daily_route() -> None:
    zones = _build(
        [
            {
                "id": "morning",
                "started_at_ms": 1000,
                "active": False,
                "completed": False,
                "segment_starts_ms": [1000],
                "points": [_point(1000, 1.0, 13), _point(2000, 2.0, 13)],
            },
            {
                "id": "after-charge",
                "started_at_ms": 10_000,
                "active": True,
                "segment_starts_ms": [10_000],
                "points": [_point(10_000, 3.0, 13), _point(11_000, 4.0, 13)],
            },
        ]
    )
    assert zones[13]["cycle_id"] == "morning"
    assert zones[13]["point_count"] == 2
    assert zones[13]["segments"] == [[[1.0, 1.0], [2.0, 1.0]]]
    assert zones[13]["active"] is False


def test_finished_charge_continuation_is_accumulated_into_same_cycle() -> None:
    zones = _build(
        [
            {
                "id": "morning",
                "started_at_ms": 1000,
                "active": False,
                "completed": False,
                "segment_starts_ms": [1000],
                "points": [_point(1000, 1.0, 13), _point(2000, 2.0, 13)],
            },
            {
                "id": "after-charge",
                "started_at_ms": 10_000,
                "active": False,
                "completed": False,
                "segment_starts_ms": [10_000],
                "points": [_point(10_000, 3.0, 13), _point(11_000, 4.0, 13)],
            },
        ]
    )
    assert zones[13]["cycle_id"] == "morning"
    assert zones[13]["point_count"] == 4
    assert zones[13]["segments"] == [
        [[1.0, 1.0], [2.0, 1.0]],
        [[3.0, 1.0], [4.0, 1.0]],
    ]


def test_completed_cycle_clears_old_route_when_new_active_cycle_enters_zone() -> None:
    zones = _build(
        [
            {
                "id": "completed-cycle",
                "started_at_ms": 1000,
                "active": False,
                "completed": True,
                "completion_reason": "dock_completed",
                "final_progress": {"13": 100},
                "segment_starts_ms": [1000],
                "points": [_point(1000, 1.0, 13), _point(2000, 2.0, 13)],
            },
            {
                "id": "new-active-cycle",
                "started_at_ms": 10_000,
                "active": True,
                "segment_starts_ms": [10_000],
                "points": [_point(10_000, 3.0, 13), _point(11_000, 4.0, 13)],
            },
        ]
    )
    assert 13 not in zones


def test_completed_cycle_is_replaced_by_later_inactive_cycle() -> None:
    zones = _build(
        [
            {
                "id": "completed-cycle",
                "started_at_ms": 1000,
                "active": False,
                "completed": True,
                "final_progress": {"13": 100},
                "segment_starts_ms": [1000],
                "points": [_point(1000, 1.0, 13), _point(2000, 2.0, 13)],
            },
            {
                "id": "new-finished-cycle",
                "started_at_ms": 10_000,
                "active": False,
                "completed": False,
                "segment_starts_ms": [10_000],
                "points": [_point(10_000, 3.0, 13), _point(11_000, 4.0, 13)],
            },
        ]
    )
    assert zones[13]["cycle_id"] == "new-finished-cycle"
    assert zones[13]["segments"] == [[[3.0, 1.0], [4.0, 1.0]]]


def test_explicit_reset_clears_only_zones_entered_by_new_cycle() -> None:
    zones = _build(
        [
            {
                "id": "morning",
                "started_at_ms": 1000,
                "active": False,
                "completed": False,
                "segment_starts_ms": [1000],
                "points": [
                    _point(1000, 1.0, 13),
                    _point(2000, 2.0, 13),
                    _point(3000, 10.0, 24),
                    _point(4000, 11.0, 24),
                ],
            },
            {
                "id": "reset-yard",
                "started_at_ms": 10_000,
                "active": True,
                "cycle_reset_zone_ids": [13, 24],
                "segment_starts_ms": [10_000],
                "points": [_point(10_000, 3.0, 13), _point(11_000, 4.0, 13)],
            },
        ]
    )
    assert 13 not in zones
    assert zones[24]["cycle_id"] == "morning"
    assert zones[24]["segments"] == [[[10.0, 1.0], [11.0, 1.0]]]


def test_partial_vendor_reset_replaces_only_reported_zone() -> None:
    zones = _build(
        [
            {
                "id": "before-reset",
                "started_at_ms": 1000,
                "active": False,
                "completed": False,
                "completion_reason": "vendor_cycle_reset_partial",
                "final_progress": {"13": 76},
                "segment_starts_ms": [1000],
                "points": [
                    _point(1000, 1.0, 13),
                    _point(2000, 2.0, 13),
                    _point(3000, 10.0, 24),
                    _point(4000, 11.0, 24),
                ],
            },
            {
                "id": "after-reset",
                "started_at_ms": 10_000,
                "active": False,
                "completed": False,
                "segment_starts_ms": [10_000],
                "points": [_point(10_000, 3.0, 13), _point(11_000, 4.0, 13)],
            },
        ]
    )
    assert zones[13]["cycle_id"] == "after-reset"
    assert zones[24]["cycle_id"] == "before-reset"
