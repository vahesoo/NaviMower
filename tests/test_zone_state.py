from __future__ import annotations

from datetime import date
import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
PACKAGE = "navimower_zone_state_test"


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
module = _load_module(f"{PACKAGE}.zone_state", COMPONENT / "zone_state.py")


def test_weighted_map_and_single_zone_task_progress() -> None:
    zones, totals = module.build_zone_model(
        map_zones=[
            {"id": 13, "name": "Yard", "area": 1000.0},
            {"id": 24, "name": "Street", "area": 100.0},
        ],
        zone_details=[
            {"id": 13, "name": "Yard", "area_m2": 1000, "percentage": 100},
            {
                "id": 24,
                "name": "Street",
                "area_m2": 100,
                "percentage": 100,
                "progress": 61,
                "progress_source": "mqtt_route",
            },
        ],
        coverage={
            "zones": [
                {"id": 13, "area": 1000, "finished": 1000, "pct": 100},
                {"id": 24, "area": 100, "finished": 61, "pct": 61},
            ]
        },
        zone_history={},
        active_session={
            "id": "cycle-street",
            "zone_ids": [24],
            "visited_zone_ids": [24],
            "task_zone_progress": {"24": 61},
        },
        active_zone_id=24,
    )
    assert totals["task_progress_pct"] == 61.0
    assert totals["task_mowed_area_m2"] == 61.0
    assert totals["map_area_m2"] == 1100.0
    assert totals["map_mowed_area_m2"] == 1061.0
    assert totals["map_coverage_pct"] == 96.5
    assert next(row for row in zones if row["id"] == 24)["cycle_id"] == "cycle-street"


def test_weighted_multi_zone_task_progress() -> None:
    _zones, totals = module.build_zone_model(
        map_zones=[
            {"id": 13, "name": "Yard", "area": 1000.0},
            {"id": 24, "name": "Street", "area": 100.0},
        ],
        zone_details=[
            {"id": 13, "name": "Yard", "area_m2": 1000, "percentage": 50},
            {"id": 24, "name": "Street", "area_m2": 100, "percentage": 10},
        ],
        coverage=None,
        zone_history={},
        active_session={
            "id": "cycle-both",
            "zone_ids": [13, 24],
            "visited_zone_ids": [13, 24],
            "task_zone_progress": {"13": 50, "24": 10},
        },
        active_zone_id=24,
    )
    assert totals["task_mowed_area_m2"] == 510.0
    assert totals["task_area_m2"] == 1100.0
    assert totals["task_progress_pct"] == 46.4


def test_unvisited_zone_keeps_daily_map_value_but_counts_zero_in_task() -> None:
    zones, totals = module.build_zone_model(
        map_zones=[
            {"id": 13, "name": "Yard", "area": 1000.0},
            {"id": 24, "name": "Street", "area": 100.0},
        ],
        zone_details=[
            {"id": 13, "name": "Yard", "area_m2": 1000, "percentage": 100},
            {"id": 24, "name": "Street", "area_m2": 100, "percentage": 20},
        ],
        coverage=None,
        zone_history={},
        active_session={
            "id": "cycle-both",
            "zone_ids": [13, 24],
            "visited_zone_ids": [24],
            "task_zone_progress": {"13": 0, "24": 20},
        },
        active_zone_id=24,
    )
    yard = next(row for row in zones if row["id"] == 13)
    assert yard["coverage_pct"] == 100.0
    assert yard["task_progress_pct"] == 0.0
    assert totals["task_progress_pct"] == 1.8
    assert totals["map_coverage_pct"] == 92.7


def test_daily_trails_keep_previous_cycle_for_unconfirmed_continuation() -> None:
    local_day = date(2026, 8, 3)
    sessions = [
        {
            "id": "morning",
            "started_at_ms": 1000,
            "active": False,
            "segment_starts_ms": [1000],
            "points": [
                [1000, 1.0, 1.0, 0, "mowing", 0, 0, 13],
                [2000, 2.0, 1.0, 0, "mowing", 0, 0, 13],
                [3000, 10.0, 1.0, 0, "mowing", 0, 0, 24],
                [4000, 11.0, 1.0, 0, "mowing", 0, 0, 24],
            ],
        },
        {
            "id": "afternoon-street",
            "started_at_ms": 5000,
            "active": True,
            "segment_starts_ms": [5000],
            "points": [
                [5000, 12.0, 1.0, 0, "mowing", 0, 0, 24],
                [6000, 13.0, 1.0, 0, "mowing", 0, 0, 24],
            ],
        },
    ]
    payload = module.build_daily_trails(
        sessions=sessions,
        map_zones=[],
        local_date=local_day,
        to_local_date=lambda _stamp: local_day,
        revision=12,
    )
    by_zone = {row["zone_id"]: row for row in payload["zones"]}
    assert by_zone[13]["cycle_id"] == "morning"
    assert by_zone[24]["cycle_id"] == "morning"
    assert by_zone[24]["segments"] == [[[10.0, 1.0], [11.0, 1.0]]]
    assert payload["revision"] == 12


def test_daily_trails_drop_non_drawable_stubs() -> None:
    local_day = date(2026, 8, 3)
    payload = module.build_daily_trails(
        sessions=[
            {
                "id": "stub",
                "started_at_ms": 1000,
                "active": False,
                "points": [[1000, 1.0, 1.0, 0, "mowing", 0, 0, 13]],
            }
        ],
        map_zones=[],
        local_date=local_day,
        to_local_date=lambda _stamp: local_day,
        revision=1,
    )
    assert payload["zones"] == []


def test_zone_revision_ignores_live_last_mowed_timestamp() -> None:
    zones = [
        {
            "id": 13,
            "name": "Yard",
            "area_m2": 1000.0,
            "coverage_pct": 50.0,
            "mowed_area_m2": 500.0,
            "task_progress_pct": 50.0,
            "active": True,
            "selected_in_task": True,
            "visited_in_task": True,
            "cycle_id": "cycle-a",
            "last_started_at": "2026-08-03T08:00:00+00:00",
            "last_mowed_at": "2026-08-03T08:01:00+00:00",
            "last_completed_at": None,
        }
    ]
    totals = {
        "map_area_m2": 1000.0,
        "map_mowed_area_m2": 500.0,
        "map_coverage_pct": 50.0,
        "task_area_m2": 1000.0,
        "task_mowed_area_m2": 500.0,
        "task_progress_pct": 50.0,
        "task_zone_ids": [13],
        "active_zone_id": 13,
        "zone_count": 1,
        "completed_zone_count": 0,
        "last_map_mowed_at": "2026-08-03T08:01:00+00:00",
        "last_map_completed_at": None,
    }
    first = module.zone_model_signature(zones, totals)
    zones[0]["last_mowed_at"] = "2026-08-03T08:02:00+00:00"
    totals["last_map_mowed_at"] = "2026-08-03T08:02:00+00:00"
    assert module.zone_model_signature(zones, totals) == first


def test_entered_new_cycle_ignores_stale_finished_area() -> None:
    zones, totals = module.build_zone_model(
        map_zones=[
            {"id": 13, "name": "Yard", "area": 1000.0},
            {"id": 24, "name": "Street", "area": 100.0},
        ],
        zone_details=[
            {"id": 13, "name": "Yard", "area_m2": 1000, "percentage": 100},
            {"id": 24, "name": "Street", "area_m2": 100, "percentage": 100},
        ],
        coverage={
            "zones": [
                {"id": 13, "area": 1000, "finished": 1000, "pct": 100},
                {"id": 24, "area": 100, "finished": 100, "pct": 100},
            ]
        },
        zone_history={},
        active_session={
            "id": "new-street-cycle",
            "zone_ids": [24],
            "visited_zone_ids": [24],
            "task_zone_progress": {"24": 0},
        },
        active_zone_id=24,
    )
    street = next(row for row in zones if row["id"] == 24)
    assert street["coverage_pct"] == 0.0
    assert street["mowed_area_m2"] == 0.0
    assert totals["map_mowed_area_m2"] == 1000.0
    assert totals["map_coverage_pct"] == 90.9


def test_vendor_overall_task_progress_is_not_replaced_by_active_zone_progress() -> None:
    zones, totals = module.build_zone_model(
        map_zones=[
            {"id": 170, "name": "Uusmaa2", "area": 902.2174},
            {"id": 73, "name": "Plats 1", "area": 660.1573},
        ],
        zone_details=[
            {"id": 170, "name": "Uusmaa2", "area_m2": 902.2174, "percentage": 100},
            {
                "id": 73,
                "name": "Plats 1",
                "area_m2": 660.1573,
                "percentage": 56,
                "progress": 56,
                "progress_source": "map_work_position",
            },
        ],
        coverage={
            "zones": [
                {"id": 170, "area": 902.2174, "finished": 902.2174, "pct": 100},
                {"id": 73, "area": 660.1573, "finished": 369.6881, "pct": 56},
            ]
        },
        zone_history={},
        active_session={
            "id": "x390-task",
            "zone_ids": [170, 73],
            "visited_zone_ids": [170, 73],
            "task_zone_progress": {"170": 100, "73": 56},
        },
        active_zone_id=73,
        task_progress_pct=48,
        task_mowed_area_m2=750.0,
        task_progress_source="private_task_percentage",
        task_area_source="private_cloud",
    )
    active = next(row for row in zones if row["id"] == 73)
    assert active["coverage_pct"] == 56.0
    assert active["task_progress_pct"] == 56.0
    assert totals["task_progress_pct"] == 48.0
    assert totals["task_mowed_area_m2"] == 750.0
    assert totals["task_progress_source"] == "private_task_percentage"
    assert totals["task_zone_progress_weighted_pct"] != totals["task_progress_pct"]
