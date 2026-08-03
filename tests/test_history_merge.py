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
    # The vendor can publish the reset during the brief non-cutting handover
    # between two scheduled passes. The old route must still be finalized.
    assert manager.prepare_cycle(reset, pose_time=2_200_000_020) is True
    assert manager._active_id is None

    manager.process_pose(
        position={"x": 1.2, "y": 1.2},
        pose_time=2_200_000_021,
        heading=0.1,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[24],
    )
    second_id = manager._active_id
    assert second_id and second_id != first_id
    assert len(manager._sessions) == 2
    first = manager._cache[first_id]
    assert first["completed"] is True
    assert first["completion_reason"] == "vendor_cycle_reset"
    assert first["final_progress"] == {"24": 98}
    assert not history._sessions_can_merge(first, manager._cache[second_id])
    zone_history = manager.zone_history()["24"]
    assert zone_history["last_completed_progress"] == 98
    assert zone_history["last_completed_at"]

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
    assert manager.prepare_cycle(low, pose_time=2_250_000_020) is True
    assert manager._active_id is None
    assert stub_id not in manager._cache
    assert manager._sessions == []
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
    if hass.tasks:
        await asyncio.gather(*hass.tasks)


asyncio.run(provisional_cycle_reset_stub_test())


def practical_completion_threshold_test() -> None:
    Store.values.clear()
    manager = history.NavimowerHistory(FakeHass(), "entry-threshold", "TEST")
    manager.process_pose(
        position={"x": 0.0, "y": 0.0},
        pose_time=2_300_000_000,
        heading=0.0,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[13],
        physical_zone_id=13,
    )
    manager.update_zone_history(
        {"zones": [{"id": 13, "pct": 97}]},
        [{"id": 13, "name": "Street", "percentage": 97}],
        active_zone_progress=97,
    )
    active = manager.active_session
    assert active is not None
    assert active["completed"] is True
    assert active["completion_reason"] == "vendor_progress"


practical_completion_threshold_test()
print("cycle tracking tests passed")


async def dock_completion_history_test() -> None:
    Store.values.clear()
    hass = FakeHass()
    manager = history.NavimowerHistory(hass, "entry-dock-complete", "TEST")
    manager.process_pose(
        position={"x": 0.0, "y": 0.0},
        pose_time=2_400_000_000,
        heading=0.0,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[24],
        physical_zone_id=24,
    )
    manager.update_zone_history(
        {"zones": [{"id": 24, "pct": 97}]},
        [{"id": 24, "name": "Yard", "progress": 97, "percentage": 23}],
        active_zone_progress=97,
    )
    manager.prepare_cycle(
        {
            "coverage": {"zones": [{"id": 24, "pct": 23}]},
            "zone_details": [{"id": 24, "name": "Yard", "progress": 97}],
        },
        pose_time=2_400_000_010,
    )
    manager.process_pose(
        position={"x": 0.1, "y": 0.1},
        pose_time=2_400_000_020,
        heading=0.0,
        activity="docked",
        cutting=False,
        docked=True,
        returning=False,
        zone_ids=[24],
        completed=True,
    )
    zone = manager.zone_history()["24"]
    assert zone["last_completed_at"]
    assert zone["last_completed_progress"] == 97
    completed = manager.session_summaries(include_points=True)[-1]
    assert completed["completion_reason"] == "vendor_progress"
    assert completed["final_progress"] == {"24": 97}
    if hass.tasks:
        await asyncio.gather(*hass.tasks)


asyncio.run(dock_completion_history_test())
print("dock completion history tests passed")


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
    assert manager.prepare_cycle(high, pose_time=2_600_000_010) is False
    assert manager.prepare_cycle(low, pose_time=2_600_000_020) is True
    first = manager._sessions[0]
    assert first["completed"] is False
    assert first["completion_reason"] == "vendor_cycle_reset_partial"
    assert "last_completed_at" not in manager.zone_history().get("13", {})
    if hass.tasks:
        await asyncio.gather(*hass.tasks)


asyncio.run(vendor_partial_reset_test())
print("explicit and vendor partial reset tests passed")
