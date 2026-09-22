"""Real store/renderer regressions; all mower geometry below is synthetic."""
import ast
import asyncio
from copy import deepcopy
import importlib
import json
from pathlib import Path
import re
import types
import xml.etree.ElementTree as ET

from aiohttp import web
import pytest

from test_vendor_trail_store import (
    DiskStorage, History, Hass, NOW, PACKAGE, ZONES, geometry, ledger, observe,
    session, store, store_module, vendor,
)

artifacts = importlib.import_module(f"{PACKAGE}.map_artifacts")
ROOT = Path(__file__).resolve().parents[1]


class CountingHass(Hass):
    def __init__(self):
        super().__init__()
        self.svg_builds = 0
        self.resource_builds = 0
        self.before_svg = None

    async def async_add_executor_job(self, func, *args):
        if func.__name__ == "build_session_svg_archive":
            self.svg_builds += 1
            if self.before_svg:
                await self.before_svg()
        if func.__name__ == "_resource":
            self.resource_builds += 1
        return await super().async_add_executor_job(func, *args)


def owner_for(store, *, entry_id="mower", sessions=None):
    store.hass = CountingHass()
    owner = types.SimpleNamespace(
        hass=store.hass, entry=types.SimpleNamespace(entry_id=entry_id),
        data={"map": {"revision": "map-1", "zones": deepcopy(ZONES)}},
        vendor_trail_store=store, history=History(sessions),
    )
    owner.history.trail_revision = 1
    owner.history.active_session_no = 1
    owner.current_cycle_render_manager = vendor.VendorTrailCurrentCycleRenderManager(owner)
    owner.map_artifacts = artifacts.MapArtifactManager(owner)
    return owner


def seed(store, both=True):
    observe(store)
    store.accept(geometry())
    if both:
        store.accept(geometry(91))


def descriptors(manager):
    return {row["zone_id"]: row for row in manager.manifest()["zones"]}


async def checkpoint(manager, zone_ids=None, reason="test"):
    task = manager.request_checkpoint(
        zone_ids=set(zone_ids) if zone_ids is not None else None,
        reason=reason,
    )
    if task is not None:
        await task
    return task


def test_prewarmed_100_reads_do_not_rebuild_or_read_history(store):
    seed(store)
    owner = owner_for(store)
    def forbidden(*args, **kwargs):
        raise AssertionError("All-vendor map must not read History")
    owner.history.session_summaries = forbidden
    async def check():
        manager = owner.map_artifacts
        await checkpoint(manager)
        assert owner.hass.svg_builds == 2
        before = manager.build_count
        resource_before = owner.hass.resource_builds
        for _ in range(100):
            render = await manager.async_get(ZONES)
            assert render["mowed_area"]["path_d"]
        assert manager.build_count == before == 1
        assert manager.cache_hits == 100
        assert owner.hass.resource_builds == resource_before == 2
        owner.history.trail_revision += 100
        store.ledger["revision"] += 10
        owner.data["activity"] = "docked"
        await manager.async_get(ZONES)
        assert manager.build_count == 1
        await manager.async_shutdown()
    asyncio.run(check())


def test_manifest_and_ready_resources_never_wait_for_slow_builder(store):
    seed(store)
    owner = owner_for(store)
    async def check():
        gate = asyncio.Event()
        started = asyncio.Event()
        async def hold():
            started.set()
            await gate.wait()
        owner.hass.before_svg = hold
        manager = owner.map_artifacts
        job = manager.request_checkpoint(reason="test")
        await started.wait()
        for _ in range(100):
            manifest = manager.manifest()
            assert manifest["building"]
            assert all(row["pending"] and row["artifact"] is None for row in manifest["zones"])
        assert not job.done()
        callers = [asyncio.create_task(manager.async_get(ZONES)) for _ in range(5)]
        await asyncio.sleep(0)
        assert owner.hass.svg_builds == 1
        gate.set()
        results = await asyncio.gather(*callers)
        assert all(result == results[0] for result in results)
        assert manager.build_count == 2
        assert manager.checkpoint_count == 1
        assert owner.hass.svg_builds == 2
        manifest = manager.manifest()
        text = json.dumps(manifest)
        assert "path_d" not in text and '"points"' not in text
        assert all(not row["pending"] for row in manifest["zones"])
        await manager.async_shutdown()
    asyncio.run(check())


def test_only_changed_zone_is_rebuilt_and_transferred(store):
    seed(store)
    owner = owner_for(store)
    async def check():
        manager = owner.map_artifacts
        await checkpoint(manager)
        before = descriptors(manager)
        old91 = manager.resource(91, before[91]["artifact"]["resource_id"])

        store.accept(geometry(end=20))
        await manager.request_refresh()
        frozen = descriptors(manager)
        assert frozen[91] == before[91]
        assert frozen[92]["artifact"]["resource_id"] == before[92]["artifact"]["resource_id"]
        assert frozen[92]["geometry_ahead"] is True
        assert owner.hass.svg_builds == 2

        await checkpoint(manager, {92}, reason="zone_exit")
        after = descriptors(manager)
        assert after[91] == before[91]
        assert after[92]["artifact"]["resource_id"] != before[92]["artifact"]["resource_id"]
        assert manager.resource(91, before[91]["artifact"]["resource_id"]) is old91
        assert owner.hass.svg_builds == 3
        assert owner.hass.resource_builds == 3
        assert manager.resource(92, before[92]["artifact"]["resource_id"])

        for end in (25, 30, 35):
            store.accept(geometry(end=end))
            await manager.request_refresh()
        assert owner.hass.svg_builds == 3
        await checkpoint(manager, {92}, reason="session_settled")
        assert owner.hass.svg_builds == 4
        assert len(manager._resources[92]) == 2
        assert manager.resource(92, before[92]["artifact"]["resource_id"]) is None
        await manager.async_shutdown()
    asyncio.run(check())


def test_geometry_advances_coalesce_and_publish_same_cycle_without_starvation(store):
    seed(store, both=False)
    owner = owner_for(store)
    async def check():
        manager = owner.map_artifacts
        gate = asyncio.Event()
        started = asyncio.Event()
        async def hold_once():
            owner.hass.before_svg = None
            started.set()
            await gate.wait()
        owner.hass.before_svg = hold_once
        job = manager.request_checkpoint(zone_ids={92}, reason="test")
        await started.wait()
        for end in (15, 20, 25, 30):
            store.accept(geometry(end=end))
            assert manager.request_checkpoint(zone_ids={92}, reason="zone_exit") is job
        gate.set()
        await asyncio.wait_for(job, 5)
        assert manager.checkpoint_coalesced_updates == 4
        assert owner.hass.svg_builds == 2
        assert manager.checkpoint_count == 2
        descriptor = descriptors(manager)[92]
        assert not descriptor["pending"]
        assert descriptor["geometry_ahead"] is False
        assert descriptor["artifact"]["geometry_revision"] == store.records[92]["geometry_revision"]
        await manager.async_shutdown()
    asyncio.run(check())


def test_reset_revokes_one_zone_and_rejects_inflight_old_resource(store):
    seed(store)
    owner = owner_for(store)
    async def check():
        manager = owner.map_artifacts
        await checkpoint(manager)
        before = descriptors(manager)
        gate = asyncio.Event()
        started = asyncio.Event()
        async def hold_once():
            owner.hass.before_svg = None
            started.set()
            await gate.wait()
        owner.hass.before_svg = hold_once
        store.accept(geometry(end=20))
        job = manager.request_checkpoint(zone_ids={92}, reason="test")
        await started.wait()
        state, _ = ledger.mark_explicit_reset(store.ledger, [92], observed_at_ms=NOW+100_000, reason="test")
        store.reconcile(state)
        assert manager.resource(92, before[92]["artifact"]["resource_id"]) is None
        assert manager.resource(91, before[91]["artifact"]["resource_id"])
        manager.request_refresh()
        gate.set()
        await asyncio.wait_for(job, 5)
        result = await manager.async_get(ZONES)
        assert result["zone_ids"] == [91]
        assert set(descriptors(manager)) == {91}
        assert not store.accept(geometry())
        await manager.async_shutdown()
    asyncio.run(check())


def test_map_revision_edit_retains_resources_but_width_change_invalidates(store):
    seed(store)
    owner = owner_for(store)
    async def check():
        manager = owner.map_artifacts
        await checkpoint(manager)
        before = descriptors(manager)
        owner.data["map"]["revision"] = "added-channel"
        await manager.request_refresh()
        assert descriptors(manager) == before
        assert owner.hass.svg_builds == 2
        owner.data["mowing_path_width_m"] = 0.5
        assert manager.resource(92, before[92]["artifact"]["resource_id"]) is None
        await manager.request_refresh()
        assert descriptors(manager)[92]["artifact"] is None
        await checkpoint(manager, reason="width_change")
        assert descriptors(manager)[92]["artifact"]["resource_id"] != before[92]["artifact"]["resource_id"]
        assert owner.hass.svg_builds == 4
        await manager.async_shutdown()
    asyncio.run(check())


def test_restart_restores_same_resource_ids_without_rasterizing_again(store):
    seed(store)
    owner = owner_for(store)
    async def check():
        manager = owner.map_artifacts
        await checkpoint(manager)
        before = descriptors(manager)
        await store.async_flush()
        await manager.async_shutdown()
        restored = store_module.VendorTrailStore(Hass(), "mower", storage=store.storage)
        await restored.async_load()
        other = owner_for(restored)
        await other.map_artifacts.request_refresh()
        assert descriptors(other.map_artifacts) == before
        assert other.hass.svg_builds == 0
        assert other.hass.resource_builds == 2
        await other.map_artifacts.async_shutdown()
    asyncio.run(check())


def test_cache_failures_are_bounded_diagnostics_are_passive_and_recovery_works(store):
    seed(store)
    owner = owner_for(store)
    async def check():
        manager = owner.map_artifacts
        original = owner.current_cycle_render_manager.async_get
        async def fail(*args):
            raise RuntimeError("synthetic failure")
        owner.current_cycle_render_manager.async_get = fail
        await manager.request_refresh()
        for _ in range(20):
            manager.manifest()
            manager.diagnostics()
        assert manager.failure_count == 1
        assert manager.build_count == 0
        with pytest.raises(artifacts.MapArtifactUnavailable):
            await manager.async_get(ZONES)
        owner.current_cycle_render_manager.async_get = original
        manager._retry_at = 0
        await checkpoint(manager, reason="recovery")
        assert manager.last_error is None
        assert manager.build_count == 1
        diagnostic = json.dumps(manager.diagnostics())
        assert "path_d" not in diagnostic and '"body"' not in diagnostic
        assert manager.diagnostics()["ready_zone_count"] == 2
        await manager.async_shutdown()
    asyncio.run(check())


def test_shutdown_cancels_worker_and_pending_http_waiters(store):
    seed(store)
    owner = owner_for(store)
    async def check():
        started = asyncio.Event()
        async def hold():
            started.set()
            await asyncio.Event().wait()
        owner.hass.before_svg = hold
        manager = owner.map_artifacts
        manager.request_checkpoint(reason="test")
        await started.wait()
        caller = asyncio.create_task(manager.async_get(ZONES))
        await asyncio.sleep(0)
        await manager.async_shutdown()
        with pytest.raises(artifacts.MapArtifactUnavailable):
            await asyncio.wait_for(caller, 1)
        assert manager._task.done()
        assert manager.request_refresh() is None
        assert manager.resource(92, "missing") is None
    asyncio.run(check())


def test_mixed_vendor_and_history_fallback_remains_available(store):
    seed(store, both=False)
    history = [session("unowned", zone=91, end=5, active=False)]
    original = deepcopy(history)
    owner = owner_for(store, sessions=history)
    async def check():
        manager = owner.map_artifacts
        await checkpoint(manager)
        result = await manager.async_get(ZONES)
        assert result["zone_ids"] == [91, 92]
        assert result["vendor_trail_debug"]["mqtt_fallback_zone_ids"] == [91]
        assert manager.manifest()["fallback_zone_ids"] == [91]
        assert history == original
        await manager.async_shutdown()
    asyncio.run(check())


def _http_function():
    source = ROOT / "custom_components/navimower/map_api_performance.py"
    tree = ast.parse(source.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_zone_artifact_response")
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function], type_ignores=[])
    env = {"re": re, "web": web}
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), env)
    return env[function.name]


def test_http_etag_privacy_and_reset_checked_before_304(store):
    seed(store)
    owner = owner_for(store)
    response_for = _http_function()
    async def check():
        manager = owner.map_artifacts
        await checkpoint(manager)
        descriptor = descriptors(manager)[92]["artifact"]
        resource_id = descriptor["resource_id"]
        request = types.SimpleNamespace(query={"zone_artifact": "92", "artifact_id": resource_id}, headers={})
        response = response_for(owner, request)
        assert response.status == 200
        assert response.content_type == "image/svg+xml"
        assert response.headers["Cache-Control"] == "private, no-cache, must-revalidate"
        assert response.headers["Vary"] == "Authorization"
        root = ET.fromstring(response.body)
        assert root.tag.endswith("svg")
        assert root[0].attrib["d"] == store.records[92]["artifact"]["mowed_area"]["path_d"]
        request.headers["If-None-Match"] = f'W/"{resource_id}"'
        assert response_for(owner, request).status == 304
        state, _ = ledger.mark_explicit_reset(store.ledger, [92], observed_at_ms=NOW+100_000, reason="test")
        store.reconcile(state)
        with pytest.raises(web.HTTPGone):
            response_for(owner, request)
        request.query["artifact_id"] = "../../secret"
        with pytest.raises(web.HTTPBadRequest):
            response_for(owner, request)
        await manager.async_shutdown()
    asyncio.run(check())


def test_same_zone_ids_in_two_mowers_never_share_resources(store, tmp_path):
    seed(store)
    other_store = store_module.VendorTrailStore(Hass(), "other", storage=DiskStorage(tmp_path / "other.json"))
    seed(other_store)
    first = owner_for(store, entry_id="first")
    second = owner_for(other_store, entry_id="second")
    async def check():
        await checkpoint(first.map_artifacts)
        await checkpoint(second.map_artifacts)
        first_id = descriptors(first.map_artifacts)[92]["artifact"]["resource_id"]
        second_id = descriptors(second.map_artifacts)[92]["artifact"]["resource_id"]
        assert first_id != second_id
        assert second.map_artifacts.resource(92, first_id) is None
        await first.map_artifacts.async_shutdown()
        await second.map_artifacts.async_shutdown()
    asyncio.run(check())
