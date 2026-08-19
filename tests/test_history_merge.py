"""Dependency-free regression tests for Navimower session merging."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import sys
import types
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def module(name: str) -> types.ModuleType:
    value = types.ModuleType(name)
    sys.modules[name] = value
    return value


homeassistant = module("homeassistant")
homeassistant.__path__ = []
core = module("homeassistant.core")
core.HomeAssistant = object
helpers = module("homeassistant.helpers")
helpers.__path__ = []
storage = module("homeassistant.helpers.storage")
util = module("homeassistant.util")
util.__path__ = []
dt = module("homeassistant.util.dt")
dt.now = lambda: datetime.now(UTC)
dt.as_local = lambda value: value.astimezone()
util.dt = dt


class Store:
    """Minimal in-memory Home Assistant Store replacement."""

    values: dict[str, Any] = {}

    def __init__(self, hass, version, key, **kwargs):
        del hass, version, kwargs
        self.key = key

    async def async_load(self):
        return self.values.get(self.key)

    async def async_save(self, value):
        self.values[self.key] = value

    async def async_remove(self):
        self.values.pop(self.key, None)

    def async_delay_save(self, factory, delay):
        del delay
        value = factory()
        if value is not None:
            self.values[self.key] = value


storage.Store = Store
module("custom_components")
navimower = module("custom_components.navimower")
navimower.__path__ = [str(ROOT / "custom_components" / "navimower")]

spec = importlib.util.spec_from_file_location(
    "custom_components.navimower.history",
    ROOT / "custom_components" / "navimower" / "history.py",
)
assert spec and spec.loader
history = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = history
spec.loader.exec_module(history)


class FakeHass:
    def __init__(self):
        self.tasks: list[asyncio.Task] = []

    def async_create_task(self, coro, name=None):
        task = asyncio.create_task(coro, name=name)
        self.tasks.append(task)
        return task


def session(identifier: str, start: int, end: int | None, *, active: bool = False):
    return {
        "sn": "TEST",
        "id": identifier,
        "sequence": int(identifier),
        "started_at_ms": start,
        "started_at": history._iso(start),
        "ended_at_ms": end,
        "ended_at": history._iso(end),
        "active": active,
        "legacy": False,
        "approximate_timestamps": False,
        "mode": "mowing",
        "zone_ids": [13],
        "cutting_height_mm": 35,
        "completed": None,
        "points": [[start, 1.0, 2.0, 0.0, "mowing", 4, 5]],
        "segment_starts_ms": [start],
    }


# Pure merge boundary checks.
base = session("1", 1_000_000, 1_060_000)
short_gap = session("2", 1_359_999, 1_420_000)
assert history._sessions_can_merge(base, short_gap)

boundary = session("3", 1_360_000, 1_420_000)
assert history._sessions_can_merge(base, boundary)

long_gap = session("4", 1_360_001, 1_420_000)
assert not history._sessions_can_merge(base, long_gap)

merged = history._merge_session_records(base, short_gap)
assert merged["id"] == "1"
assert merged["started_at_ms"] == 1_000_000
assert merged["ended_at_ms"] == 1_420_000
assert merged["segment_starts_ms"] == [1_000_000, 1_359_999]
assert len(merged["points"]) == 2
merged_card = history._card_session(merged, include_points=True)
assert merged_card["points"] == [[1.0, 2.0], [1.0, 2.0]]
assert merged_card["segments"] == [[[1.0, 2.0]], [[1.0, 2.0]]]

active = session("5", 1_200_000, None, active=True)
merged_active = history._merge_session_records(base, active)
assert merged_active["active"] is True
assert merged_active["ended_at_ms"] is None

legacy = session("6", 1_100_000, 1_120_000)
legacy["legacy"] = True
assert not history._sessions_can_merge(base, legacy)


async def runtime_resume_test() -> None:
    Store.values.clear()
    hass = FakeHass()
    manager = history.NavimowerHistory(hass, "entry", "TEST")

    manager.process_pose(
        position={"x": 1.0, "y": 2.0},
        pose_time=2_000_000_000,
        heading=0.0,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[13],
    )
    original_id = manager._active_id
    original_sequence = manager._sequence
    assert original_id

    manager.process_pose(
        position={"x": 1.1, "y": 2.1},
        pose_time=2_000_000_060,
        heading=0.0,
        activity="docked",
        cutting=False,
        docked=True,
        returning=False,
        zone_ids=[13],
    )
    assert manager._active_id is None

    # Resume four minutes later: same id/sequence, a second segment.
    manager.process_pose(
        position={"x": 3.0, "y": 4.0},
        pose_time=2_000_000_300,
        heading=0.1,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[24],
    )
    assert manager._active_id == original_id
    assert manager._sequence == original_sequence
    current = manager.active_session
    assert current is not None
    assert current["active"] is True
    assert current["zone_ids"] == [13, 24]
    assert len(current["segment_starts_ms"]) == 2
    assert manager.active_trail_segments_xy() == [
        [[1.0, 2.0], [1.1, 2.1]],
        [[3.0, 4.0]],
    ]

    # Let the earlier asynchronous finalizer run. It must not overwrite resume.
    if hass.tasks:
        await asyncio.gather(*hass.tasks)
    stored = Store.values[f"navimower_session_entry_{original_id}"]
    assert stored["active"] is True
    assert stored["ended_at_ms"] is None


asyncio.run(runtime_resume_test())


async def finalize_in_flight_resume_test() -> None:
    Store.values.clear()
    hass = FakeHass()
    manager = history.NavimowerHistory(hass, "entry-race", "TEST")

    manager.process_pose(
        position={"x": 1.0, "y": 2.0},
        pose_time=2_100_000_000,
        heading=0.0,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[13],
    )
    session_id = manager._active_id
    assert session_id
    store = manager._session_store_for(session_id)
    save_started = asyncio.Event()
    allow_save = asyncio.Event()
    calls = 0

    async def blocked_save(value):
        nonlocal calls
        calls += 1
        if calls == 1:
            save_started.set()
            await allow_save.wait()
        Store.values[store.key] = value

    store.async_save = blocked_save

    manager.process_pose(
        position={"x": 1.1, "y": 2.1},
        pose_time=2_100_000_060,
        heading=0.0,
        activity="docked",
        cutting=False,
        docked=True,
        returning=False,
        zone_ids=[13],
    )
    await save_started.wait()

    manager.process_pose(
        position={"x": 3.0, "y": 4.0},
        pose_time=2_100_000_180,
        heading=0.1,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[24],
    )
    allow_save.set()
    await asyncio.gather(*hass.tasks)

    stored = Store.values[store.key]
    assert calls >= 2
    assert stored["active"] is True
    assert stored["ended_at_ms"] is None
    assert stored["zone_ids"] == [13, 24]


asyncio.run(finalize_in_flight_resume_test())


async def persisted_repair_test() -> None:
    Store.values.clear()
    first = session("1", 2_000_000_000_000, 2_000_000_060_000)
    second = session("2", 2_000_000_120_000, None, active=True)
    Store.values["navimower_sessions_entry2"] = {
        "sn": "TEST",
        "sequence": 2,
        "active_id": "2",
        "sessions": [history._metadata(first), history._metadata(second)],
        "zone_history": {},
    }
    Store.values["navimower_session_entry2_1"] = first
    Store.values["navimower_session_entry2_2"] = second

    manager = history.NavimowerHistory(FakeHass(), "entry2", "TEST")
    await manager.async_load()

    summaries = manager.session_summaries(include_points=True)
    assert len(summaries) == 1
    assert manager._active_id == "1"
    assert summaries[0]["active"] is True
    assert summaries[0]["segment_count"] == 2
    assert summaries[0]["segments"] == [
        [[1.0, 2.0]],
        [[1.0, 2.0]],
    ]
    assert "navimower_session_entry2_2" not in Store.values


asyncio.run(persisted_repair_test())
print("history merge tests passed")


async def cycle_reset_split_test() -> None:
    Store.values.clear()
    hass = FakeHass()
    manager = history.NavimowerHistory(hass, "entry-cycle", "TEST")

    manager.process_pose(
        position={"x": 1.0, "y": 1.0},
        pose_time=2_200_000_000,
        heading=0.0,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[24],
    )
    first_id = manager._active_id
    assert first_id
    manager.process_pose(
        position={"x": 1.05, "y": 1.05},
        pose_time=2_200_000_005,
        heading=0.0,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[24],
    )

    high = {
        "coverage": {
            "zones": [{"id": 24, "name": "Yard", "pct": 98, "start_time": 2_199_999_000}],
        },
        "zone_details": [{"id": 24, "name": "Yard", "progress": 98, "percentage": 98}],
    }
    assert manager.prepare_cycle(high, pose_time=2_200_000_010) is False

    reset = {
        "coverage": {
            "zones": [{"id": 24, "name": "Yard", "pct": 4, "start_time": 2_200_000_020}],
        },
        "zone_details": [{"id": 24, "name": "Yard", "progress": 4, "percentage": 4}],
    }
    reset["active_zone_progress_zone_id"] = 24
    assert manager.prepare_cycle(reset, pose_time=2_200_000_020) is True
    assert manager._active_id == first_id

    manager.process_pose(
        position={"x": 1.2, "y": 1.2}, pose_time=2_200_000_021, heading=0.1, activity="mowing", cutting=True, docked=False, returning=False, zone_ids=[24],
    )
    assert manager._active_id == first_id
    assert len(manager._sessions) == 1
    first = manager._cache[first_id]
    assert first["completed"] is None
    assert first["completion_reason"] is None
    boundaries = first.get("zone_cycle_boundaries") or []
    assert len(boundaries) == 1
    assert boundaries[0]["zone_id"] == 24
    assert boundaries[0]["previous_peak"] == 98
    zone_history = manager.zone_history().get("24", {})
    assert "last_completed_progress" not in zone_history
    assert "last_completed_at" not in zone_history

    if hass.tasks:
        await asyncio.gather(*hass.tasks)


asyncio.run(cycle_reset_split_test())


async def provisional_cycle_reset_stub_test() -> None:
    """An immediate reset must discard a non-drawable startup stub."""
    Store.values.clear()
    hass = FakeHass()
    manager = history.NavimowerHistory(hass, "entry-provisional", "TEST")
    manager.process_pose(
        position={"x": 4.0, "y": 4.0},
        pose_time=2_250_000_000,
        heading=0.0,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[24],
    )
    stub_id = manager._active_id
    assert stub_id
    high = {
        "coverage": {"zones": [{"id": 24, "name": "Yard", "pct": 98}]},
        "zone_details": [{"id": 24, "name": "Yard", "progress": 98}],
    }
    low = {
        "coverage": {"zones": [{"id": 24, "name": "Yard", "pct": 4}]},
        "zone_details": [{"id": 24, "name": "Yard", "progress": 4}],
    }
    assert manager.prepare_cycle(high, pose_time=2_250_000_010) is False
    low["active_zone_progress_zone_id"] = 24
    assert manager.prepare_cycle(low, pose_time=2_250_000_020) is True
    assert manager._active_id == stub_id
    assert stub_id in manager._cache
    assert len(manager._sessions) == 1
    manager.process_pose(
        position={"x": 4.1, "y": 4.1},
        pose_time=2_250_000_021,
        heading=0.1,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[24],
    )
    assert len(manager._sessions) == 1
    assert manager._active_id == stub_id
    assert (manager._cache[stub_id].get("zone_cycle_boundaries") or [])[0]["zone_id"] == 24
    if hass.tasks:
        await asyncio.gather(*hass.tasks)


asyncio.run(provisional_cycle_reset_stub_test())



def _coverage_snapshot(zone_rows, *, target, physical, activity="mowing", age=0):
    return {
        "coverage": {"zones": zone_rows},
        "zone_details": [
            {
                "id": row["id"],
                "name": row.get("name", f"Zone {row['id']}"),
                "percentage": row.get("pct"),
                "vendor_percentage": row.get("pct"),
                "vendor_start_time": row.get("start_time"),
                "vendor_end_time": row.get("end_time"),
            }
            for row in zone_rows
        ],
        "coverage_source_age": age,
        "active_zone_progress_zone_id": target,
        "current_physical_zone_id": physical,
        "activity": activity,
    }


def vendor_coverage_completion_test() -> None:
    Store.values.clear()
    manager = history.NavimowerHistory(FakeHass(), "entry-coverage", "TEST")
    manager.process_pose(
        position={"x": 0.0, "y": 0.0}, pose_time=2_300_000_000,
        heading=0.0, activity="mowing", cutting=True, docked=False,
        returning=False, zone_ids=[13], physical_zone_id=13,
    )
    low = _coverage_snapshot(
        [{"id": 13, "name": "Street", "pct": 90,
          "start_time": 2_300_000_000, "end_time": 2_300_000_010}],
        target=13, physical=13,
    )
    manager.prepare_cycle(low, pose_time=2_300_000_010)
    manager.update_from_snapshot(low)
    assert "last_completed_at" not in manager.zone_history()["13"]

    done = _coverage_snapshot(
        [{"id": 13, "name": "Street", "pct": 100,
          "start_time": 2_300_000_000, "end_time": 2_300_000_020}],
        target=13, physical=13,
    )
    manager.prepare_cycle(done, pose_time=2_300_000_020)
    manager.update_from_snapshot(done)
    active = manager.active_session
    assert active is not None
    assert active["completed"] is True
    assert active["completion_reason"] == "vendor_coverage"
    zone = manager.zone_history()["13"]
    assert zone["last_completed_progress"] == 100
    assert zone["last_completed_source"] == "private_zone_coverage"
    assert zone["last_completed_confirmation"] == "coverage_100_after_incomplete"


vendor_coverage_completion_test()


def private_live_100_never_completes_test() -> None:
    Store.values.clear()
    manager = history.NavimowerHistory(FakeHass(), "entry-live-private", "TEST")
    manager.process_pose(
        position={"x": 1.0, "y": 1.0}, pose_time=2_410_000_000,
        heading=0.0, activity="mowing", cutting=True, docked=False,
        returning=False, zone_ids=[24], physical_zone_id=24,
    )
    partial = _coverage_snapshot(
        [{"id": 24, "name": "Yard", "pct": 25,
          "start_time": 2_410_000_000, "end_time": 2_410_000_010}],
        target=24, physical=24,
    )
    manager.prepare_cycle(partial, pose_time=2_410_000_010)
    manager.update_from_snapshot(partial)
    manager.update_zone_history(
        partial["coverage"], partial["zone_details"],
        active_zone_progress=100, active_progress_zone_id=24,
        active_zone_progress_source="private_map_work_position",
        active_zone_progress_source_age=0,
        task_progress=100, task_progress_source="mqtt_task_percentage",
        task_progress_source_age=0, target_zone_ids=[24],
        physical_zone_id=24, coverage_source_age=0,
        activity="mowing", observed_at=2_410_000_020,
    )
    assert "last_completed_at" not in manager.zone_history()["24"]


private_live_100_never_completes_test()


def stale_coverage_100_never_completes_test() -> None:
    Store.values.clear()
    manager = history.NavimowerHistory(FakeHass(), "entry-stale-coverage", "TEST")
    manager.process_pose(
        position={"x": 2.0, "y": 2.0}, pose_time=2_420_000_000,
        heading=0.0, activity="mowing", cutting=True, docked=False,
        returning=False, zone_ids=[13], physical_zone_id=13,
    )
    stale = _coverage_snapshot(
        [{"id": 13, "name": "Street", "pct": 100,
          "start_time": 2_200_000_000, "end_time": 2_200_000_100}],
        target=13, physical=13,
    )
    manager.prepare_cycle(stale, pose_time=2_420_000_010)
    manager.update_from_snapshot(stale)
    assert "last_completed_at" not in manager.zone_history()["13"]
    diag = manager.cycle_diagnostics()["active_completion"]
    assert diag["candidates"]["13"]["reason"] == "coverage_100_without_current_cycle_evidence"


stale_coverage_100_never_completes_test()


def multi_zone_target_transition_completion_test() -> None:
    Store.values.clear()
    manager = history.NavimowerHistory(FakeHass(), "entry-multi-zone", "TEST")
    manager.process_pose(
        position={"x": 3.0, "y": 3.0}, pose_time=2_430_000_000,
        heading=0.0, activity="mowing", cutting=True, docked=False,
        returning=False, zone_ids=[24, 13], physical_zone_id=24,
    )
    first = _coverage_snapshot(
        [
            {"id": 24, "name": "Yard", "pct": 97,
             "start_time": 2_430_000_000, "end_time": 2_430_000_010},
            {"id": 13, "name": "Street", "pct": 0,
             "start_time": 2_430_000_000, "end_time": 0},
        ],
        target=24, physical=24,
    )
    manager.prepare_cycle(first, pose_time=2_430_000_010)
    manager.update_from_snapshot(first)
    assert "last_completed_at" not in manager.zone_history()["24"]

    second = _coverage_snapshot(
        [
            {"id": 24, "name": "Yard", "pct": 100,
             "start_time": 2_430_000_000, "end_time": 2_430_000_020},
            {"id": 13, "name": "Street", "pct": 20,
             "start_time": 2_430_000_015, "end_time": 2_430_000_020},
        ],
        target=13, physical=13,
    )
    manager.prepare_cycle(second, pose_time=2_430_000_020)
    manager.update_from_snapshot(second)
    zone = manager.zone_history()["24"]
    assert zone["last_completed_progress"] == 100
    assert zone["last_completed_source"] == "private_zone_coverage"
    diag = manager.cycle_diagnostics()["active_completion"]
    assert 24 in diag["confirmed"]
    assert 13 in diag["seen_incomplete"]


multi_zone_target_transition_completion_test()


def end_time_below_100_never_completes_test() -> None:
    Store.values.clear()
    manager = history.NavimowerHistory(FakeHass(), "entry-end-time", "TEST")
    manager.process_pose(
        position={"x": 4.0, "y": 4.0}, pose_time=2_440_000_000,
        heading=0.0, activity="mowing", cutting=True, docked=False,
        returning=False, zone_ids=[13], physical_zone_id=13,
    )
    partial = _coverage_snapshot(
        [{"id": 13, "name": "Street", "pct": 97,
          "start_time": 2_440_000_000, "end_time": 2_440_000_020}],
        target=13, physical=13, activity="returning",
    )
    manager.prepare_cycle(partial, pose_time=2_440_000_021)
    manager.update_from_snapshot(partial)
    assert "last_completed_at" not in manager.zone_history()["13"]


end_time_below_100_never_completes_test()
print("beta21 coverage completion tests passed")


async def explicit_partial_reset_test() -> None:
    """A successful reset command splits even a 50% pass without completing it."""
    Store.values.clear()
    hass = FakeHass()
    manager = history.NavimowerHistory(hass, "entry-explicit-reset", "TEST")
    manager.process_pose(
        position={"x": 2.0, "y": 2.0},
        pose_time=2_500_000_000,
        heading=0.0,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[24],
    )
    first_id = manager._active_id
    assert first_id
    manager.process_pose(
        position={"x": 2.05, "y": 2.05},
        pose_time=2_500_000_005,
        heading=0.0,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[24],
    )
    fifty = {
        "coverage": {"zones": [{"id": 24, "name": "Yard", "pct": 50}]},
        "zone_details": [{"id": 24, "name": "Yard", "progress": 50}],
    }
    assert manager.prepare_cycle(fifty, pose_time=2_500_000_010) is False
    assert manager.start_new_cycle(
        pose_time=2_500_000_020,
        zone_ids=[24],
        reason="navimower.mow_reset",
    ) is True
    assert manager._active_id is None
    first = manager._cache[first_id]
    assert first["completed"] is False
    assert first["completion_reason"] == "navimower.mow_reset"
    assert first["final_progress"] == {"24": 50}
    assert "last_completed_at" not in manager.zone_history().get("24", {})

    manager.process_pose(
        position={"x": 2.1, "y": 2.1},
        pose_time=2_500_000_021,
        heading=0.1,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[24],
    )
    second_id = manager._active_id
    assert second_id and second_id != first_id
    assert not history._sessions_can_merge(first, manager._cache[second_id])
    diag = manager.cycle_diagnostics()
    assert diag["last_event"]["reason"] == "navimower.mow_reset"
    assert diag["last_event"]["completed"] is False
    if hass.tasks:
        await asyncio.gather(*hass.tasks)


asyncio.run(explicit_partial_reset_test())


async def vendor_partial_reset_test() -> None:
    """An app-side 50% -> 3% reset starts a new cycle but not last-completed."""
    Store.values.clear()
    hass = FakeHass()
    manager = history.NavimowerHistory(hass, "entry-app-reset", "TEST")
    manager.process_pose(
        position={"x": 3.0, "y": 3.0},
        pose_time=2_600_000_000,
        heading=0.0,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[13],
    )
    manager.process_pose(
        position={"x": 3.05, "y": 3.05},
        pose_time=2_600_000_005,
        heading=0.0,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[13],
    )
    high = {
        "coverage": {"zones": [{"id": 13, "name": "Street", "pct": 50}]},
        "zone_details": [{"id": 13, "name": "Street", "progress": 50}],
    }
    low = {
        "coverage": {"zones": [{"id": 13, "name": "Street", "pct": 3}]},
        "zone_details": [{"id": 13, "name": "Street", "progress": 3}],
    }
    high["active_zone_progress_zone_id"] = 13
    low["active_zone_progress_zone_id"] = 13
    first_id = manager._active_id
    assert manager.prepare_cycle(high, pose_time=2_600_000_010) is False
    assert manager.prepare_cycle(low, pose_time=2_600_000_020) is True
    assert manager._active_id == first_id
    assert len(manager._sessions) == 1
    first = manager._cache[first_id]
    assert first["completed"] is None
    assert first["completion_reason"] is None
    boundaries = first.get("zone_cycle_boundaries") or []
    assert len(boundaries) == 1
    assert boundaries[0]["zone_id"] == 13
    assert boundaries[0]["previous_peak"] == 50
    assert "last_completed_at" not in manager.zone_history().get("13", {})
    if hass.tasks:
        await asyncio.gather(*hass.tasks)


asyncio.run(vendor_partial_reset_test())
print("explicit and vendor partial reset tests passed")


async def beta18_stale_high_error_return_test() -> None:
    Store.values.clear()
    hass = FakeHass()
    manager = history.NavimowerHistory(hass, "entry-beta18-error", "TEST")
    manager.process_pose(position={"x": 5.0, "y": 5.0}, pose_time=2_700_000_000, heading=0.0,
        activity="mowing", cutting=True, docked=False, returning=False, zone_ids=[24], physical_zone_id=24)
    manager.update_zone_history(
        {"zones": [{"id": 24, "pct": 98}]},
        [{"id": 24, "name": "Yard", "progress": 98, "percentage": 98,
          "last_completed_at": history._iso(2_700_000_010_000)}],
        active_zone_progress=98, active_progress_zone_id=24)
    active = manager.active_session
    assert active and active["completed"] is not True
    assert active["task_zone_completion_confirmed"] == []
    assert "last_completed_at" not in manager.zone_history()["24"]
    manager.process_pose(position={"x": 5.1, "y": 5.1}, pose_time=2_700_000_020, heading=0.0,
        activity="docked", cutting=False, docked=True, returning=False, zone_ids=[24], completed=True)
    assert "last_completed_at" not in manager.zone_history()["24"]
    assert manager.session_summaries(include_points=True)[-1]["completed"] is False
    if hass.tasks:
        await asyncio.gather(*hass.tasks)

asyncio.run(beta18_stale_high_error_return_test())

async def beta18_persisted_false_completion_repair_test() -> None:
    Store.values.clear()
    reset_ms = 2_800_000_020_000
    reset_session = session("1", 2_800_000_000_000, 2_800_000_030_000)
    reset_session["zone_ids"] = [24]
    reset_session["zone_cycle_boundaries"] = [{"zone_id": 24, "at_ms": reset_ms, "reason": "vendor_progress_reset"}]
    Store.values["navimower_sessions_entry-beta18-repair"] = {
        "sn": "TEST", "sequence": 1, "active_id": None,
        "sessions": [history._metadata(reset_session)],
        "zone_history": {
            "13": {"id": 13, "last_completed_at": history._iso(2_800_000_010_000)},
            "24": {"id": 24, "last_completed_at": history._iso(reset_ms), "last_completed_progress": 98},
            "42": {"id": 42, "last_completed_at": history._iso(2_799_000_000_000), "last_completed_progress": 97},
        },
    }
    Store.values["navimower_session_entry-beta18-repair_1"] = reset_session
    manager = history.NavimowerHistory(FakeHass(), "entry-beta18-repair", "TEST")
    await manager.async_load()
    repaired = manager.zone_history()
    assert "last_completed_at" not in repaired["13"]
    assert "last_completed_at" not in repaired["24"]
    assert repaired["42"]["last_completed_progress"] == 97
    assert repaired["42"]["last_completed_at"]

asyncio.run(beta18_persisted_false_completion_repair_test())
print("beta18 completion safety tests passed")
