"""Behavior regressions for persistent zone/cycle ownership (no HA dependency).

i108 zone 92 and Tont docked/empty-task are user-reported scenario shapes,
not captured beta8 diagnostics; all coordinates/identifiers below are synthetic.
"""
import asyncio
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "vendor_store_regression"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "custom_components/navimower")]
sys.modules[PACKAGE] = package
ledger = importlib.import_module(f"{PACKAGE}.zone_ledger")
vendor = importlib.import_module(f"{PACKAGE}.vendor_trail")
store_module = importlib.import_module(f"{PACKAGE}.vendor_trail_store")
render = importlib.import_module(f"{PACKAGE}.vendor_trail_render_semantics")
svg = importlib.import_module(f"{PACKAGE}.session_svg")

NOW = 1_800_000_000_000
ZONES = [
    {"id": zone, "area": 100, "polygon": [[0, y], [30, y], [30, y+10], [0, y+10]]}
    for zone, y in [(91, 20), (92, 0)]
]


class DiskStorage:
    def __init__(self, path):
        self.path = path

    async def async_load(self):
        return json.loads(self.path.read_text()) if self.path.exists() else None

    async def async_save(self, value):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value))
        tmp.replace(self.path)

    def async_delay_save(self, getter, delay):
        self.pending = getter


class Hass:
    def __init__(self):
        self.builds = []
        self.executor_calls = []

    async def async_add_executor_job(self, func, *args):
        self.executor_calls.append(func.__name__)
        if func.__name__ == "build_session_svg_archive":
            self.builds.append(deepcopy(args[0]))
        return func(*args)


class History:
    def __init__(self, sessions=None):
        self.sessions = sessions or []

    def session_summaries(self, include_points=False):
        return [{key: deepcopy(value) for key, value in session.items() if key != "points"} for session in self.sessions]

    async def async_session_payload(self, session_id):
        return deepcopy(next(session for session in self.sessions if session["id"] == session_id))


@pytest.fixture
def store(tmp_path):
    return store_module.VendorTrailStore(Hass(), "mower", storage=DiskStorage(tmp_path / "store.json"))


def observe(store, pct=60, *, zones=None, start=100, observation=1, activity="mowing", task=None):
    snapshot = {
        "activity": activity, "current_zone_ids": [92] if task is None else task,
        "active_zone_progress_zone_id": 92 if activity == "mowing" else None,
        "coverage": {"zones": [{"id": 91, "pct": 100, "start_time": 100}, {"id": 92, "pct": pct, "start_time": start}]},
        "coverage_source_age": 0, "coverage_observation_id": observation,
    }
    state, _, _, _, events = ledger.reduce_zone_ledger(
        store.ledger, snapshot=snapshot, map_zones=zones or ZONES,
        zone_details=[], zone_history={}, active_session=None,
        observed_at_ms=NOW + observation*1000,
    )
    store.reconcile(state)
    return snapshot, events


def geometry(zone=92, end=10, start=100):
    return vendor.normalize_vendor_trail_row({
        "partitionId": zone, "startTime": start, "partitionPercentage": 60,
        "points": [{"x": x, "y": 0 if zone == 92 else 20} for x in range(end+1)],
    })


def session(name="s1", zone=92, start=0, end=25, stamp=NOW+10_000, active=True):
    points = [[stamp+x*1000, x, 0 if zone == 92 else 20, 0, "mowing", 4, 5, zone] for x in range(start, end+1)]
    return {"id": name, "active": active, "started_at_ms": points[0][0],
            "point_count": len(points), "points": points, "zone_ids": [zone],
            "segment_starts_ms": [points[0][0]], "completed": True}


def get_render(store, sessions=None):
    coordinator = types.SimpleNamespace(hass=store.hass, data={}, vendor_trail_store=store, history=History(sessions))
    manager = vendor.VendorTrailCurrentCycleRenderManager(coordinator)
    return asyncio.run(render._authoritative_async_get(manager, ZONES))


@pytest.mark.parametrize("activity,task", [("mowing", [91]), ("docked", []), ("idle", []), ("paused", [92])])
def test_task_membership_and_docking_retain_artifact(store, activity, task):
    observe(store)
    store.accept(geometry())
    before = get_render(store)["mowed_area"]
    observe(store, activity=activity, task=task, observation=2)
    assert get_render(store)["mowed_area"] == before
    assert store.owned_zone_ids() == {92}
    assert vendor.current_vendor_rows({"current_zone_ids": task}, store.records)
    if activity in {"docked", "idle"}:
        assert vendor.active_vendor_row({"activity": activity}, store.records) is None


def test_empty_error_and_shorter_poll_keep_last_good(store):
    observe(store)
    store.accept(geometry())
    before = get_render(store)
    assert not store.accept({"zone_id": 92, "points": []})
    assert not store.accept(geometry(end=3))
    # Failed transport supplies no observations, never a deletion.
    assert vendor.decode_vendor_trail_response(None) == []
    with pytest.raises(ValueError):
        vendor.decode_vendor_trail_response("not-zstd")
    assert get_render(store) == before


def test_map_edit_same_coverage_retains_but_zero_resets_only_one_zone(store):
    observe(store)
    store.accept(geometry())
    store.accept(geometry(91))
    get_render(store)
    old = deepcopy(store.records)
    changed = deepcopy(ZONES)
    changed[1]["polygon"][1][0] = 31
    observe(store, zones=changed, observation=2)
    assert store.records == old
    assert len(store.hass.builds) == 2
    _, events = observe(store, pct=0, zones=ZONES, observation=3)
    assert any(e["type"] == "zone_cycle_reset" for e in events)
    assert 92 not in store.records
    assert store.records[91] == old[91]
    assert store.ledger["zones"]["92"]["cycle_key"] != old[92]["cycle_id"]
    assert not store.accept(geometry())  # old compressed path while coverage is 0
    result = get_render(store, [session(active=False, stamp=NOW-100_000)])
    assert result["zone_ids"] == [91]


def test_repeated_mqtt_reads_are_not_two_vendor_reset_confirmations(store):
    observe(store)
    store.accept(geometry())
    observe(store, pct=0, observation=2)
    observe(store, pct=0, observation=2)
    assert 92 in store.records
    observe(store, pct=0, observation=3)
    assert 92 not in store.records


def test_restart_restores_ledger_geometry_svg_and_ownership(store):
    observe(store)
    store.accept(geometry())
    before = get_render(store)
    asyncio.run(store.async_flush())
    restored = store_module.VendorTrailStore(Hass(), "mower", storage=store.storage)
    asyncio.run(restored.async_load())
    assert restored.export() == store.export()
    assert get_render(restored) == before
    assert restored.hass.builds == []


def test_vendor_revision_rebuilds_only_changed_zone_including_interior_points(store):
    observe(store)
    store.accept(geometry())
    store.accept(geometry(91))
    get_render(store)
    assert len(store.hass.builds) == 2
    row = geometry()
    row["points"][4][1] = 1  # same count and endpoint, different geometry
    store.accept(row)
    get_render(store)
    assert len(store.hass.builds) == 3
    assert {p[7] for p in store.hass.builds[-1]["points"]} == {92}


def test_history_fallback_point_arbitration_runs_in_executor(store):
    observe(store)
    sessions = [session("history", zone=91, end=20, active=False)]
    result = get_render(store, sessions)
    assert result["source_point_count"] == len(sessions[0]["points"])
    assert "_build_vendor_fallback_source" in store.hass.executor_calls


def test_same_cycle_combines_history_sessions_until_vendor_adoption(store):
    observe(store)
    sessions = [session("first", end=5, active=False), session("second", start=6, end=9, stamp=NOW+100_000, active=False)]
    original = deepcopy(sessions)
    fallback = get_render(store, sessions)
    assert fallback["source_point_count"] == 10
    assert sessions == original
    archive = svg.build_session_svg_archive(sessions[0])
    store.accept(geometry(end=9))
    adopted = get_render(store, sessions)
    assert adopted["source_point_count"] == 10
    assert adopted["vendor_trail_debug"]["mqtt_fallback_zone_ids"] == []
    after = svg.build_session_svg_archive(sessions[0])
    assert after["mowed_area"] == archive["mowed_area"]
    assert after["route"] == archive["route"]


def test_sticky_ownership_even_when_svg_generation_fails(store, monkeypatch):
    observe(store)
    store.accept(geometry())
    monkeypatch.setattr(svg, "build_session_svg_archive", lambda *args: None)
    result = get_render(store, [session(active=False)])
    assert result["mowed_area"]["path_d"] == ""
    assert result["vendor_trail_debug"]["mqtt_base_suppressed_for_zone_ids"] == [92]
    assert result["vendor_trail_debug"]["mqtt_fallback_zone_ids"] == []


def test_i108_zone92_tail_exceeds_8m_then_vendor_confirms_prefix(store):
    snap, _ = observe(store)
    store.accept(geometry(end=10))
    live = session(end=30)
    store.update_live_tail(snap, live)
    assert store.live_tail(92) == [[[x, 0] for x in range(10, 31)]]
    store.accept(geometry(end=20))
    store.update_live_tail(snap, live)
    assert store.live_tail(92) == [[[x, 0] for x in range(20, 31)]]
    # A repeated cached snapshot cannot bring the confirmed prefix back.
    store.update_live_tail(snap, live)
    assert store.live_tail(92)[0][0] == [20, 0]


def test_unmatched_adoption_never_uses_full_session_but_new_live_points_accumulate(store):
    snap, _ = observe(store)
    row = geometry()
    row["points"] = [[p[0], 100] for p in row["points"]]
    store.accept(row)
    store.update_live_tail(snap, session(end=20))
    assert store.live_tail(92) == [[[20, 0]]]
    store.update_live_tail(snap, session(end=40))
    assert store.live_tail(92) == [[[x, 0] for x in range(20, 41)]]


def test_tail_preserves_session_and_zone_gaps(store):
    snap, _ = observe(store)
    store.accept(geometry(end=2))
    live = session(end=6)
    live["points"][4][7] = 91
    store.update_live_tail(snap, live)
    assert store.live_tail(92) == [[[2, 0], [3, 0]], [[5, 0], [6, 0]]]
    store.update_live_tail(snap, session("s2", start=7, end=9, stamp=NOW+100_000))
    assert store.live_tail(92)[-1] == [[7, 0], [8, 0], [9, 0]]


def test_geometry_from_old_cycle_cannot_be_relabelled_by_fresh_coverage(store):
    observe(store, start=200)
    row = vendor.normalize_vendor_trail_row({"partitionId": 92, "startTime": 100,
        "points": [{"x": 0, "y": 0}, {"x": 1, "y": 0}]}, coverage={"pct": 60, "start_time": 200})
    assert not store.accept(row)


def test_reset_while_rendering_does_not_publish_old_svg(store):
    observe(store)
    store.accept(geometry())
    original = store.hass.async_add_executor_job
    async def reset_during_executor(func, *args):
        result = await original(func, *args)
        state, _ = ledger.mark_explicit_reset(store.ledger, [92], observed_at_ms=NOW+1000, reason="test-reset")
        store.reconcile(state)
        store.hass.async_add_executor_job = original
        return result
    store.hass.async_add_executor_job = reset_during_executor
    result = get_render(store)
    assert result["mowed_area"]["path_d"] == ""
    assert result["zone_ids"] == []


def test_reset_while_loading_fallback_does_not_publish_retained_old_svg(store):
    observe(store)
    store.accept(geometry())
    get_render(store)
    class ResetHistory(History):
        async def async_session_payload(self, session_id):
            if 92 in store.records:
                state, _ = ledger.mark_explicit_reset(store.ledger, [92], observed_at_ms=NOW+100_000, reason="reset")
                store.reconcile(state)
            return await super().async_session_payload(session_id)
    owner = types.SimpleNamespace(hass=store.hass, data={}, vendor_trail_store=store, history=ResetHistory([session(end=2, active=False)]))
    manager = vendor.VendorTrailCurrentCycleRenderManager(owner)
    result = asyncio.run(manager.async_get(ZONES))
    assert result["mowed_area"]["path_d"] == ""


def test_svg_error_keeps_last_good_artifact_and_never_uses_session_fallback(store, monkeypatch):
    observe(store)
    store.accept(geometry())
    old = get_render(store)["mowed_area"]
    store.accept(geometry(end=15))
    def fail(*args):
        raise RuntimeError("simulated rendering failure")
    monkeypatch.setattr(svg, "build_session_svg_archive", fail)
    assert get_render(store, [session(active=False)])["mowed_area"] == old


def test_two_mowers_with_same_zone_id_keep_independent_storage(store, tmp_path):
    other = store_module.VendorTrailStore(Hass(), "other", storage=DiskStorage(tmp_path / "other.json"))
    observe(store)
    observe(other, start=200)
    store.accept(geometry())
    other.accept(geometry(start=200, end=3))
    asyncio.run(store.async_flush())
    asyncio.run(other.async_flush())
    assert other.records[92]["cycle_id"] != store.records[92]["cycle_id"]
    assert len(other.records[92]["points"]) == 4
    assert len(store.records[92]["points"]) == 11
