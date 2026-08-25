"""Regression coverage for reset-based per-zone current-cycle trails."""
from __future__ import annotations

from datetime import date
import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
PACKAGE = "navimower_beta41_cycle_trail_test"


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

TODAY = date(2026, 8, 25)
YESTERDAY = date(2026, 8, 24)


def _point(stamp: int, x: float, zone_id: int) -> list[object]:
    return [stamp, x, 1.0, 0.0, "mowing", 4, -1, zone_id]


def _build(sessions: list[dict]) -> dict[int, dict]:
    payload = zone_state.build_daily_trails(
        sessions=sessions,
        map_zones=[],
        local_date=TODAY,
        to_local_date=lambda stamp: YESTERDAY if stamp < 10_000 else TODAY,
        revision=41,
    )
    assert payload["scope"] == "current_cycle"
    return {row["zone_id"]: row for row in payload["zones"]}


def test_same_cycle_survives_calendar_midnight() -> None:
    zones = _build(
        [
            {
                "id": "before-midnight",
                "started_at_ms": 1000,
                "active": False,
                "completed": False,
                "segment_starts_ms": [1000],
                "points": [_point(1000, 1.0, 13), _point(2000, 2.0, 13)],
            },
            {
                "id": "after-midnight",
                "started_at_ms": 10_000,
                "active": False,
                "completed": False,
                "segment_starts_ms": [10_000],
                "points": [_point(10_000, 3.0, 13), _point(11_000, 4.0, 13)],
            },
        ]
    )

    assert zones[13]["cycle_id"] == "before-midnight"
    assert zones[13]["point_count"] == 4
    assert zones[13]["segments"] == [
        [[1.0, 1.0], [2.0, 1.0]],
        [[3.0, 1.0], [4.0, 1.0]],
    ]


def test_confirmed_completion_still_starts_next_cycle_only_on_zone_entry() -> None:
    zones = _build(
        [
            {
                "id": "old-cycle",
                "started_at_ms": 1000,
                "active": False,
                "completed": True,
                "completion_reason": "vendor_coverage",
                "final_progress": {"13": 100},
                "segment_starts_ms": [1000],
                "points": [
                    _point(1000, 1.0, 13),
                    _point(2000, 2.0, 13),
                    _point(3000, 10.0, 24),
                    _point(4000, 11.0, 24),
                ],
            },
            {
                "id": "next-cycle",
                "started_at_ms": 10_000,
                "active": False,
                "completed": False,
                "segment_starts_ms": [10_000],
                "points": [_point(10_000, 3.0, 13), _point(11_000, 4.0, 13)],
            },
        ]
    )

    assert zones[13]["cycle_id"] == "next-cycle"
    assert zones[13]["segments"] == [[[3.0, 1.0], [4.0, 1.0]]]
    assert zones[24]["cycle_id"] == "old-cycle"
    assert zones[24]["segments"] == [[[10.0, 1.0], [11.0, 1.0]]]


def test_active_reset_clears_only_zone_actually_entered() -> None:
    zones = _build(
        [
            {
                "id": "retained-cycle",
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
                "id": "reset-task",
                "started_at_ms": 10_000,
                "active": True,
                "cycle_reset_zone_ids": [13, 24],
                "segment_starts_ms": [10_000],
                "points": [_point(10_000, 3.0, 13), _point(11_000, 4.0, 13)],
            },
        ]
    )

    assert 13 not in zones
    assert zones[24]["cycle_id"] == "retained-cycle"
    assert zones[24]["segments"] == [[[10.0, 1.0], [11.0, 1.0]]]
