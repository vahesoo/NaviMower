"""Regression coverage for Navimower 0.4.5-beta23 checkpoint rendering."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path

from test_map_artifacts_beta14 import (
    checkpoint,
    descriptors,
    owner_for,
    seed,
)
from test_prepared_render_model_beta21 import (
    _FakeCoordinator,
    _wait_tasks,
    prepared,
)
from test_vendor_trail_store import (
    DiskStorage,
    Hass,
    geometry,
    observe,
    session,
    store_module,
)

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
MANIFEST = COMPONENT / "manifest.json"
RELEASE_NOTE = ROOT / ".github" / "release-notes" / "0.4.5-beta23.md"


def test_vendor_geometry_advances_without_expensive_artifact_rebuild(tmp_path) -> None:
    store = store_module.VendorTrailStore(
        Hass(),
        "mower",
        storage=DiskStorage(tmp_path / "store.json"),
    )
    seed(store, both=False)
    owner = owner_for(store)

    async def check() -> None:
        manager = owner.map_artifacts
        await checkpoint(manager, reason="startup")
        first = descriptors(manager)[92]
        first_id = first["artifact"]["resource_id"]
        assert owner.hass.svg_builds == 1

        for end in (15, 20, 25, 30):
            assert store.accept(geometry(end=end))
            await manager.request_refresh()

        frozen = descriptors(manager)[92]
        assert frozen["artifact"]["resource_id"] == first_id
        assert frozen["geometry_ahead"] is True
        assert frozen["pending"] is False
        assert owner.hass.svg_builds == 1
        assert manager.diagnostics()["dirty_zone_count"] == 1

        manager.observe({"activity": "mowing", "current_physical_zone_id": 92})
        manager.observe({"activity": "returning", "current_physical_zone_id": 92})
        assert manager._checkpoint_task is None

        manager.observe({"activity": "docked", "current_physical_zone_id": None})
        assert manager._checkpoint_task is not None
        await manager._checkpoint_task

        final = descriptors(manager)[92]
        assert final["artifact"]["resource_id"] != first_id
        assert final["geometry_ahead"] is False
        assert owner.hass.svg_builds == 2
        diagnostics = manager.diagnostics()
        assert diagnostics["mode"] == "event_checkpoint_plus_mqtt_live_tail"
        assert diagnostics["checkpoint_count"] == 2
        assert diagnostics["checkpoint_zone_build_count"] == 2
        assert diagnostics["last_checkpoint_reason"] == "session_settled"
        assert diagnostics["checkpoint_failure_count"] == 0
        assert diagnostics["dirty_zone_count"] == 0
        await manager.async_shutdown()

    asyncio.run(check())


def test_zone_exit_is_a_checkpoint_but_same_zone_updates_are_not(tmp_path) -> None:
    store = store_module.VendorTrailStore(
        Hass(),
        "mower",
        storage=DiskStorage(tmp_path / "store.json"),
    )
    seed(store, both=True)
    owner = owner_for(store)

    async def check() -> None:
        manager = owner.map_artifacts
        await checkpoint(manager, reason="startup")
        assert owner.hass.svg_builds == 2

        assert store.accept(geometry(end=20))
        manager.observe({"activity": "mowing", "current_physical_zone_id": 92})
        manager.observe({"activity": "mowing", "current_physical_zone_id": 92})
        assert manager._checkpoint_task is None
        assert owner.hass.svg_builds == 2

        manager.observe({"activity": "mowing", "current_physical_zone_id": 91})
        assert manager._checkpoint_task is not None
        await manager._checkpoint_task
        assert owner.hass.svg_builds == 3
        assert manager.diagnostics()["last_checkpoint_reason"] == "zone_exit"
        assert descriptors(manager)[92]["geometry_ahead"] is False
        await manager.async_shutdown()

    asyncio.run(check())


def test_live_tail_stays_anchored_to_published_checkpoint_until_next_build(tmp_path) -> None:
    store = store_module.VendorTrailStore(
        Hass(),
        "mower",
        storage=DiskStorage(tmp_path / "store.json"),
    )
    observe(store)
    assert store.accept(geometry(end=2))
    live = session(end=6)

    store.update_live_tail({}, live)
    assert store.live_tail(92) == [[[x, 0] for x in range(2, 7)]]

    asyncio.run(store.async_artifacts(0.25, build=True, zone_ids={92}))
    store.update_live_tail({}, live)
    assert store.live_tail(92) == [[[x, 0] for x in range(2, 7)]]
    first_artifact_revision = tuple(store.records[92]["artifact_revision"])

    assert store.accept(geometry(end=4))
    store.update_live_tail({}, live)
    # New vendor geometry is retained, but the visible live tail still starts
    # at the endpoint of the last published base.
    assert tuple(store.records[92]["artifact_revision"]) == first_artifact_revision
    assert store.live_tail(92) == [[[x, 0] for x in range(2, 7)]]

    asyncio.run(store.async_artifacts(0.25, build=True, zone_ids={92}))
    store.update_live_tail({}, live)
    assert store.live_tail(92) == [[[x, 0] for x in range(4, 7)]]
    assert tuple(store.records[92]["artifact_revision"]) != first_artifact_revision


def test_static_render_hash_ignores_runtime_georeference_diagnostics() -> None:
    async def check() -> None:
        old_interval = prepared.LIVE_PREPARE_MIN_INTERVAL_SECONDS
        prepared.LIVE_PREPARE_MIN_INTERVAL_SECONDS = 0.0
        try:
            owner = _FakeCoordinator()
            owner.data["map"] = deepcopy(owner.data["map"])
            owner.data["map"]["georeference"] = {
                "schema_version": 1,
                "source": "cloud_location_fit",
                "status": "validated",
                "map_revision": "map-r7",
                "geodesy_model": "wgs84_ellipsoid_v1",
                "reference": {
                    "local_x": 1.0,
                    "local_y": 2.0,
                    "latitude": 59.0,
                    "longitude": 24.0,
                },
                "rotation_rad": 0.25,
                "calibration": {"sample_count": 24, "adaptive_replacement_count": 1},
                "validation": {"valid": True, "report_time": 100},
                "last_refinement_result": {"accepted": True, "report_time": 100},
                "cartographic_frame": {"coordinate_epoch": 100},
                "vendor_hint": {"validation": {"report_time": 100}},
                "local_frame_check": {"sample_count": 4},
                "reference_candidates": {"count": 2},
            }

            manager = prepared.PreparedRenderModelManager(owner)
            manager.start()
            await _wait_tasks(manager)
            first = manager.diagnostics()
            assert first["static_build_count"] == 1
            first_id = first["static_resource_id"]

            geo = owner.data["map"]["georeference"]
            geo["calibration"]["adaptive_replacement_count"] = 99
            geo["validation"]["report_time"] = 999
            geo["last_refinement_result"]["report_time"] = 999
            geo["cartographic_frame"]["coordinate_epoch"] = 999
            geo["vendor_hint"]["validation"]["report_time"] = 999
            geo["local_frame_check"]["sample_count"] = 20
            geo["reference_candidates"]["count"] = 9
            manager.request_refresh()
            await _wait_tasks(manager)

            stable = manager.diagnostics()
            assert stable["static_build_count"] == 1
            assert stable["static_resource_id"] == first_id

            owner.data["map"]["zones"][0]["polygon"][1][0] = 11
            manager.request_refresh()
            await _wait_tasks(manager)
            geometry_changed = manager.diagnostics()
            assert geometry_changed["static_build_count"] == 2
            assert geometry_changed["static_resource_id"] != first_id

            second_id = geometry_changed["static_resource_id"]
            geo["reference"]["latitude"] = 59.00001
            manager.request_refresh()
            await _wait_tasks(manager)
            transform_changed = manager.diagnostics()
            assert transform_changed["static_build_count"] == 3
            assert transform_changed["static_resource_id"] != second_id

            await manager.async_shutdown()
        finally:
            prepared.LIVE_PREPARE_MIN_INTERVAL_SECONDS = old_interval

    asyncio.run(check())


def test_beta23_release_metadata_and_checkpoint_wiring() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"].startswith("0.4.5-beta")

    note = RELEASE_NOTE.read_text(encoding="utf-8")
    assert note.startswith("title: Navimower 0.4.5-beta23")
    assert "checkpoint" in note.lower()
    assert "MQTT live tail" in note
    assert "static" in note.lower()

    coordinator = (COMPONENT / "coordinator_semantics.py").read_text(encoding="utf-8")
    assert 'request_checkpoint(reason="startup")' in coordinator
    assert "artifacts.observe(snapshot)" in coordinator

    artifact_source = (COMPONENT / "map_artifacts.py").read_text(encoding="utf-8")
    assert "event_checkpoint_plus_mqtt_live_tail" in artifact_source
    assert '"geometry_ahead"' in artifact_source
