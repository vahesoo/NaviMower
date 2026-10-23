"""Regression tests for confirmed zone-membership and command-write issues."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "navimower"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger = load("zone_ledger_beta24", "zone_ledger.py")
writer = load("setting_write_beta24", "setting_write.py")


def zone(i):
    return {
        "id": i,
        "name": f"Zone {i}",
        "area": 100,
        "polygon": [[i * 20, 0], [i * 20 + 10, 0],
                    [i * 20 + 10, 10], [i * 20, 10]],
    }


def observe(state, ids, *, identified=True, coverage_ids=None):
    zones = [zone(i) for i in ids]
    snap = {
        "coverage": {
            "zones": [
                {"id": i, "area": 100, "finished": 100, "pct": 100, "start_time": 100}
                for i in (coverage_ids if coverage_ids is not None else ids)
            ]
        },
        "coverage_source_age": 1,
        "activity": "docked",
        "docked": True,
    }
    if identified:
        snap["map"] = {
            "revision": "map1-v2" if len(ids) == 3 else "map1-v1",
            "map_id": "map1",
            "map_base_id": "base1",
            "zones": zones,
        }
    return ledger.reduce_zone_ledger(
        state, snapshot=snap, map_zones=zones, zone_details=[],
        zone_history={}, active_session=None, observed_at_ms=1_800_000_000_000,
    )


def test_retired_zone_does_not_pollute_current_totals_or_restart():
    state, _, totals, _, _ = observe(None, [1, 2, 3, 4])
    assert totals["map_area_m2"] == 400
    state, rows, totals, _, _ = observe(
        state, [2, 3, 4], coverage_ids=[1, 2, 3, 4],
    )
    assert [r["id"] for r in rows] == [2, 3, 4]
    assert totals["map_area_m2"] == 300
    assert totals["map_coverage_pct"] == 100
    assert "1" in state["zones"]
    restored = ledger.normalize_ledger_state(state)
    assert restored["active_map"]["zone_ids"] == [2, 3, 4]
    restored, rows, totals, _, _ = observe(
        restored, [], identified=False, coverage_ids=[],
    )
    assert [r["id"] for r in rows] == [2, 3, 4]
    assert totals["map_area_m2"] == 300


def test_partial_map_must_not_retire_known_zones():
    state, _, _, _, _ = observe(None, [1, 2, 3, 4])
    state, rows, totals, _, _ = observe(state, [1, 2], identified=False)
    assert {r["id"] for r in rows} == {1, 2, 3, 4}
    assert totals["map_area_m2"] == 400


@pytest.mark.asyncio
async def test_pending_to_answered_async(monkeypatch):
    calls = []
    async def no_wait(_seconds):
        return None
    monkeypatch.setattr(writer.asyncio, "sleep", no_wait)

    class Hass:
        async def async_add_executor_job(self, func, *args):
            return func(*args)
        def async_create_task(self, coro, *_args):
            return asyncio.create_task(coro)

    def status(_sn, _number):
        calls.append(1)
        return {"status": "2" if len(calls) == 1 else "3"}

    owner = SimpleNamespace(
        hass=Hass(), sn="synthetic", entry=SimpleNamespace(entry_id="test"),
        client=SimpleNamespace(command_status=status),
        _setting_write_verification={
            "schema_version": 2, "keys": {
                "mode": {"status": "pending", "device_command": {}}
            },
        },
    )
    writer._schedule_command_status(owner, "123", ["mode"])
    await owner._setting_command_tasks["mode"]
    assert len(calls) == 2
    assert owner._setting_write_verification["keys"]["mode"]["device_command"]["outcome"] == "answered"


@pytest.mark.asyncio
async def test_partial_settings_failure_is_recorded_without_replay(monkeypatch):
    async def no_wait(_seconds):
        return None
    monkeypatch.setattr(writer.asyncio, "sleep", no_wait)
    monkeypatch.setattr(writer, "_schedule_readback", lambda *_args, **_kw: None)
    monkeypatch.setattr(writer, "_schedule_command_status", lambda *_args, **_kw: None)

    class Hass:
        async def async_add_executor_job(self, func, *args):
            return func(*args)

    owner = SimpleNamespace(
        hass=Hass(), sn="synthetic", entry=SimpleNamespace(entry_id="test"),
        _endpoint_status={},
    )
    used = []
    def send_setting_device():
        used.append("device")
        return {"cmd_num": "123"}
    def persist():
        used.append("persist")
        raise RuntimeError("cloud temporarily unavailable")

    with pytest.raises(RuntimeError):
        await writer.async_write_settings(
            owner, operations=[(send_setting_device, ()), (persist, ())],
            cache_values={"mode": 2},
        )
    assert used == ["device", "persist"]
    row = owner._setting_write_verification["keys"]["mode"]
    assert row["status"] == "partial_failure"
    assert row["completed_stages"] == 1
    assert row["failed_stage"] == 2
    assert row["device_command"]["command_number_received"] is True
