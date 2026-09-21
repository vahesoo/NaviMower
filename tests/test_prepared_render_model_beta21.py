"""Regression coverage for the beta21 prepared backend render model."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
TARGET = COMPONENT / "prepared_render_model.py"


def _module(name: str) -> types.ModuleType:
    value = sys.modules.get(name)
    if value is None:
        value = types.ModuleType(name)
        sys.modules[name] = value
    return value


_module("custom_components").__path__ = [str(ROOT / "custom_components")]
pkg = _module("custom_components.navimower")
pkg.__path__ = [str(COMPONENT)]

spec = importlib.util.spec_from_file_location(
    "custom_components.navimower.prepared_render_model",
    TARGET,
)
assert spec and spec.loader
prepared = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = prepared
spec.loader.exec_module(prepared)


def _source() -> dict:
    return {
        "map": {
            "revision": "map-r7",
            "map_version": "7",
            "modified_count": 3,
            "zones": [
                {
                    "id": 5,
                    "name": "Yard",
                    "area": 80,
                    "polygon": [[0, 0], [10, 0], [10, 8], [0, 8]],
                },
                {
                    "id": 91,
                    "name": "Street",
                    "area": 12,
                    "polygon": [[-4, 1], [-2, 1], [-2, 7], [-4, 7]],
                },
            ],
            "off_limit_areas": [
                [[4, 3], [5, 3], [5, 4], [4, 4]],
            ],
            "vf_off_areas": [
                [[7, 5], [8, 5], [8, 6], [7, 6]],
            ],
            "channels": [
                {
                    "id": 2,
                    "name": "Channel",
                    "points": [[0, 4], [-2, 4], [-3, 4]],
                    "connection": [5, 91],
                    "tunnel_type": 1,
                }
            ],
            "station": {
                "x": 1.0,
                "y": 1.0,
                "direction": 0.25,
                "width": 0.6,
                "length": 0.8,
            },
        },
        "gate_areas": [
            {
                "name": "Legacy gate",
                "x_min": -6,
                "x_max": -4.5,
                "y_min": 0,
                "y_max": 2,
            },
            {
                "id": "poly",
                "name": "Polygon gate",
                "polygon": [[9, 2], [11, 2], [11, 3], [9, 3]],
            },
        ],
        "custom_areas": [
            {
                "id": "custom-a",
                "name": "Keep dry",
                "source": "test",
                "polygon": [[2, 5], [3, 5], [3, 6], [2, 6]],
            }
        ],
    }


def test_static_model_prepares_style_independent_geometry_and_card_layout() -> None:
    model = prepared.build_static_render_model(_source())

    assert model["schema_version"] == 1
    assert model["coordinate_space"] == "map_xy_m"
    assert model["geometry_summary"]["parity_ok"] is True
    assert model["geometry_summary"]["zones"] == 2
    assert model["geometry_summary"]["off_limit_areas"] == 1
    assert model["geometry_summary"]["vf_off_areas"] == 1
    assert model["geometry_summary"]["channels"] == 1
    assert model["geometry_summary"]["gate_areas"] == 2
    assert model["geometry_summary"]["custom_areas"] == 1

    yard = model["layers"]["zones"][0]
    assert yard["id"] == 5
    assert yard["path_d"] == "M0 0L10 0L10 8L0 8Z"
    assert yard["bounds"] == [0.0, 0.0, 10.0, 8.0]
    assert yard["centroid"] == [5.0, 4.0]
    assert "polygon" not in yard
    assert "points" not in yard

    channel = model["layers"]["channels"][0]
    assert channel["path_d"] == "M0 4L-2 4L-3 4"
    assert channel["connection"] == [5, 91]
    assert "points" not in channel

    no_gate = model["layout"]["without_gate_areas"]
    with_gate = model["layout"]["with_gate_areas"]
    assert no_gate["raw_bounds"] == [-4.0, 0.0, 10.0, 8.0]
    assert with_gate["raw_bounds"] == [-6.0, 0.0, 11.0, 8.0]
    assert no_gate["padding_ratio"] == 0.05
    assert no_gate["view_size"] == 1000.0
    assert no_gate["matrix"][0] > 0
    assert no_gate["matrix"][3] < 0
    assert no_gate["matrix"][1:3] == [0.0, 0.0]


def test_prepared_resource_identity_is_content_stable() -> None:
    model = prepared.build_static_render_model(_source())
    first = prepared._encode_resource("static", model, "entry-1")
    second = prepared._encode_resource("static", model, "entry-1")

    assert first["resource_id"] == second["resource_id"]
    assert first["body"] == second["body"]
    assert first["descriptor"]["byte_length"] == len(first["body"])
    assert (
        first["descriptor"]["url"]
        == f"/api/navimower/map/entry-1?static_render_model={first['resource_id']}"
    )

    changed = _source()
    changed["map"]["revision"] = "map-r8"
    changed_model = prepared.build_static_render_model(changed)
    third = prepared._encode_resource("static", changed_model, "entry-1")
    assert third["resource_id"] != first["resource_id"]


def test_live_route_model_turns_segments_into_small_svg_ready_paths() -> None:
    model = prepared.build_live_route_render_model(
        {
            "trail_segments": [
                [[0, 0], [1, 0], [2, 1]],
                [[10, 10], [11, 10]],
                [[99, 99]],
            ],
            "trail_session": 12,
            "trail_active": True,
            "activity": "mowing",
            "current_physical_zone_id": 5,
        }
    )

    assert model["segment_count"] == 2
    assert model["point_count"] == 5
    assert model["invalid_segment_count"] == 1
    assert model["bounds"] == [0.0, 0.0, 11.0, 10.0]
    assert model["segments"][0]["path_d"] == "M0 0L1 0L2 1"
    assert all("points" not in row for row in model["segments"])


class _FakeHass:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()

    async def async_add_executor_job(self, func, *args):
        return await self.loop.run_in_executor(None, func, *args)

    def async_create_background_task(self, coro, name, eager_start=False):
        del name, eager_start
        return asyncio.create_task(coro)


class _FakeEntry:
    entry_id = "entry-test"
    options = {}


class _FakeHistory:
    active_session_no = 3
    trail_revision = 10


class _FakeStore:
    revision = 7


class _FakeChannel:
    def as_dict(self):
        return {
            "name": "Gate",
            "polygon": [[-1, -1], [1, -1], [1, 0], [-1, 0]],
        }


class _FakeCoordinator:
    def __init__(self) -> None:
        self.hass = _FakeHass()
        self.entry = _FakeEntry()
        self.history = _FakeHistory()
        self.vendor_trail_store = _FakeStore()
        self.channels = [_FakeChannel()]
        self.data = {
            **_source(),
            "activity": "mowing",
            "trail_active": True,
            "current_physical_zone_id": 5,
        }
        self.data["map"] = _source()["map"]
        self._listener = None
        self.live_segments = [[[0, 0], [1, 0]]]

    def async_add_listener(self, callback):
        self._listener = callback
        return lambda: setattr(self, "_listener", None)

    def _map_payload_with_sessions(self, sessions, daily):
        del sessions, daily
        return {
            "trail_segments": self.live_segments,
            "trail_session": self.history.active_session_no,
            "trail_active": self.data["trail_active"],
        }


async def _wait_tasks(manager) -> None:
    for _ in range(100):
        tasks = [
            task
            for task in (manager._static_task, manager._live_task)
            if task is not None and not task.done()
        ]
        if not tasks and manager._live_timer is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("prepared render manager did not settle")


def test_manager_prewarms_and_only_rebuilds_static_geometry_when_needed() -> None:
    async def check() -> None:
        old_interval = prepared.LIVE_PREPARE_MIN_INTERVAL_SECONDS
        prepared.LIVE_PREPARE_MIN_INTERVAL_SECONDS = 0.0
        try:
            owner = _FakeCoordinator()
            manager = prepared.PreparedRenderModelManager(owner)
            manager.start()
            await _wait_tasks(manager)

            first = manager.diagnostics()
            assert first["prewarm_started"] is True
            assert first["static_build_count"] == 1
            assert first["live_build_count"] == 1
            assert first["static_summary"]["parity_ok"] is True
            first_static_id = first["static_resource_id"]
            first_live_id = first["live_resource_id"]

            manager.request_refresh()
            await _wait_tasks(manager)
            same = manager.diagnostics()
            assert same["static_build_count"] == 1
            assert same["live_build_count"] == 1

            owner.history.trail_revision += 1
            owner.live_segments = [[[0, 0], [2, 0], [3, 1]]]
            manager.request_refresh()
            await _wait_tasks(manager)
            live_changed = manager.diagnostics()
            assert live_changed["static_build_count"] == 1
            assert live_changed["live_build_count"] == 2
            assert live_changed["live_resource_id"] != first_live_id

            owner.data["map"] = dict(owner.data["map"])
            owner.data["map"]["revision"] = "map-r8"
            manager.request_refresh()
            await _wait_tasks(manager)
            static_changed = manager.diagnostics()
            assert static_changed["static_build_count"] == 2
            assert static_changed["static_resource_id"] != first_static_id

            manifest = manager.manifest()
            assert manifest["static"]["resource_id"] == static_changed["static_resource_id"]
            assert manifest["live_route"]["resource_id"] == static_changed["live_resource_id"]
            assert manifest["current_cycle_manifest_url"].endswith("?artifacts_only=1")
            assert manifest["history_index_url"].endswith("/entry-test")
            assert manifest["capabilities"]["static_svg_paths"] is True
            assert manifest["capabilities"]["history_render_archive"] is True

            await manager.async_shutdown()
            assert manager.diagnostics()["prewarm_started"] is False
        finally:
            prepared.LIVE_PREPARE_MIN_INTERVAL_SECONDS = old_interval

    asyncio.run(check())


def test_integration_contract_exposes_resources_and_cached_diagnostics() -> None:
    api = (COMPONENT / "map_api_performance.py").read_text(encoding="utf-8")
    coordinator = (COMPONENT / "coordinator_semantics.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")

    assert '"prepared_render_model" = ' not in api
    assert 'payload["prepared_render_model"] = prepared.discovery()' in api
    assert '"render_model_manifest"' in api
    assert '"static_render_model"' in api
    assert '"live_route_render"' in api
    assert "If-None-Match" in api
    assert "private, no-cache, must-revalidate" in api

    assert "PreparedRenderModelManager" in coordinator
    assert "self.hass.loop.call_soon(self.prepared_render_model.start)" in coordinator
    assert "await self.prepared_render_model.async_shutdown()" in coordinator
    assert 'diagnostics["prepared_render_model"] = prepared.diagnostics()' in coordinator

    assert '"prepared_render_model": prepared_render_diagnostics' in diagnostics
    assert "cached-only counters/summaries" in diagnostics
