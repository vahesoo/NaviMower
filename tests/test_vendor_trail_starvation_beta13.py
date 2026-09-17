"""Regression tests for beta13 same-cycle vendor SVG publication."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "vendor_starvation_beta13"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "custom_components/navimower")]
sys.modules[PACKAGE] = package

ledger = importlib.import_module(f"{PACKAGE}.zone_ledger")
vendor = importlib.import_module(f"{PACKAGE}.vendor_trail")
store_module = importlib.import_module(f"{PACKAGE}.vendor_trail_store")
render = importlib.import_module(f"{PACKAGE}.vendor_trail_render_semantics")

NOW = 1_800_000_000_000
ZONES = [{"id": 92, "area": 100, "polygon": [[0, 0], [30, 0], [30, 10], [0, 10]]}]


class Storage:
    async def async_load(self):
        return None

    async def async_save(self, value):
        self.value = deepcopy(value)

    def async_delay_save(self, getter, delay):
        self.pending = getter


class Hass:
    def __init__(self):
        self.builds = []
        self.after_build = None

    async def async_add_executor_job(self, func, *args):
        self.builds.append(deepcopy(args[0]))
        result = func(*args)
        callback = self.after_build
        self.after_build = None
        if callback is not None:
            callback()
        return result


class History:
    def session_summaries(self, include_points=False):
        return []

    async def async_session_payload(self, session_id):
        raise AssertionError("no history sessions expected")


def observe(store, *, pct=20, start=100, observation=1):
    snapshot = {
        "activity": "mowing",
        "current_zone_ids": [92],
        "active_zone_progress_zone_id": 92,
        "coverage": {"zones": [{"id": 92, "pct": pct, "start_time": start}]},
        "coverage_source_age": 0,
        "coverage_observation_id": observation,
    }
    state, *_ = ledger.reduce_zone_ledger(
        store.ledger,
        snapshot=snapshot,
        map_zones=ZONES,
        zone_details=[],
        zone_history={},
        active_session=None,
        observed_at_ms=NOW + observation * 1000,
    )
    store.reconcile(state)


def geometry(end, *, start=100):
    return vendor.normalize_vendor_trail_row(
        {
            "partitionId": 92,
            "startTime": start,
            "partitionPercentage": 20,
            "points": [{"x": x, "y": 0} for x in range(end + 1)],
        }
    )


def get_render(store):
    owner = types.SimpleNamespace(
        hass=store.hass,
        data={},
        vendor_trail_store=store,
        history=History(),
    )
    manager = vendor.VendorTrailCurrentCycleRenderManager(owner)
    return asyncio.run(render._authoritative_async_get(manager, ZONES))


def test_same_cycle_geometry_advance_does_not_starve_first_svg():
    hass = Hass()
    store = store_module.VendorTrailStore(hass, "mower", storage=Storage())
    observe(store)
    assert store.accept(geometry(10))
    cycle_id = store.records[92]["cycle_id"]

    def advance_same_cycle():
        assert store.accept(geometry(15))
        assert store.records[92]["cycle_id"] == cycle_id

    hass.after_build = advance_same_cycle
    first = get_render(store)

    # The first completed same-cycle SVG is immediately publishable even though
    # a newer vendor geometry revision arrived while it was rendering.
    assert len(hass.builds) == 1
    assert first["mowed_area"]["path_d"]
    assert first["zone_ids"] == [92]
    assert store.records[92]["artifact"] is not None
    assert store.records[92]["artifact_revision"][1] != store.records[92]["geometry_revision"]

    # A later request catches up to the newer geometry instead of keeping the
    # prefix forever.
    second = get_render(store)
    assert len(hass.builds) == 2
    assert second["source_point_count"] == 16
    assert store.records[92]["artifact_revision"][1] == store.records[92]["geometry_revision"]


def test_cycle_identity_ignores_geometry_revision_but_changes_on_reset():
    hass = Hass()
    store = store_module.VendorTrailStore(hass, "mower", storage=Storage())
    observe(store)
    before = render._cycle_identity(store)
    assert store.accept(geometry(10))
    assert store.accept(geometry(15))
    assert render._cycle_identity(store) == before

    state, _ = ledger.mark_explicit_reset(
        store.ledger,
        [92],
        observed_at_ms=NOW + 10_000,
        reason="beta13-test",
    )
    store.reconcile(state)
    assert render._cycle_identity(store) != before
