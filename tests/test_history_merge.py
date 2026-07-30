"""Dependency-free regression tests for Navimower session merging."""
from __future__ import annotations

import asyncio
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


module("homeassistant")
core = module("homeassistant.core")
core.HomeAssistant = object
module("homeassistant.helpers")
storage = module("homeassistant.helpers.storage")


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
    assert "navimower_session_entry2_2" not in Store.values


asyncio.run(persisted_repair_test())
print("history merge tests passed")
