"""Regression coverage for Navimower 0.4.5-beta22."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import types

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
RELEASE_NOTE = ROOT / ".github" / "release-notes" / "0.4.5-beta22.md"


def _load_location_module():
    target = COMPONENT / "location.py"
    spec = importlib.util.spec_from_file_location("beta22_location", target)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_schedule_module():
    package_name = "beta22_schedule_regression"
    package = types.ModuleType(package_name)
    package.__path__ = [str(COMPONENT)]
    sys.modules[package_name] = package

    schedule_stub = types.ModuleType(f"{package_name}.navimower_schedule")
    schedule_stub.NavimowerScheduleController = type(
        "NavimowerScheduleController",
        (),
        {},
    )
    schedule_stub._utc_now = lambda: "2026-09-22T12:00:00+00:00"
    sys.modules[schedule_stub.__name__] = schedule_stub

    logic_stub = types.ModuleType(f"{package_name}.schedule_logic")
    logic_stub.parse_iso = lambda value: None
    sys.modules[logic_stub.__name__] = logic_stub

    previous = {
        name: sys.modules.get(name)
        for name in ("homeassistant", "homeassistant.util", "homeassistant.util.dt")
    }
    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    util = types.ModuleType("homeassistant.util")
    util.__path__ = []
    dt_module = types.ModuleType("homeassistant.util.dt")
    dt_module.now = lambda: object()
    util.dt = dt_module
    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.util"] = util
    sys.modules["homeassistant.util.dt"] = dt_module

    try:
        target = COMPONENT / "schedule_dispatch_weather_semantics.py"
        spec = importlib.util.spec_from_file_location(
            f"{package_name}.schedule_dispatch_weather_semantics",
            target,
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def test_type4_task_delay_has_per_message_freshness_flag() -> None:
    location = _load_location_module()
    cache: dict[str, dict] = {}

    delayed = location.parse_location_payload(
        cache,
        "mower",
        [{"type": 4, "taskDelay": True, "vehicleState": 1, "time": 123}],
    )
    assert delayed is not None
    assert delayed["task_delay"] is True
    assert delayed["_task_delay_updated"] is True

    pose = location.parse_location_payload(
        cache,
        "mower",
        [{
            "type": 1,
            "postureX": 1.0,
            "postureY": 2.0,
            "postureTheta": 0.0,
            "vehicleState": 1,
            "time": 124,
        }],
    )
    assert pose is not None
    assert pose["task_delay"] is True
    assert pose["_task_delay_updated"] is False


def test_cross_window_stale_task_delay_does_not_block_retained_resume() -> None:
    semantics = _load_schedule_module()

    class Center:
        @staticmethod
        def _mqtt_value(key):
            return True if key == "task_delay" else None

    class Coordinator:
        def __init__(self):
            self.notification_center = Center()
            self._notification_cache = {}
            self.age = 600.0

        def mqtt_task_delay_age(self):
            return self.age

    class Controller:
        def __init__(self):
            self.coordinator = Coordinator()
            self._runtime = {
                "resume_pending": True,
                "interrupted_reason": "rain",
                "weather_wait_window_token": "2026-09-21",
            }

        @staticmethod
        def _window_state(now):
            del now
            return True, "2026-09-22"

    controller = Controller()
    assert semantics._weather_delay_reason(controller, since=None) == (None, None)

    controller.coordinator.age = 30.0
    assert semantics._weather_delay_reason(controller, since=None) == (
        "vendor_task_delay",
        None,
    )

    controller.coordinator.age = 600.0
    controller._runtime["weather_wait_window_token"] = "2026-09-22"
    assert semantics._weather_delay_reason(controller, since=None) == (
        "vendor_task_delay",
        None,
    )


def test_vendor_live_tail_requires_cutting_action_even_if_activity_says_mowing(tmp_path) -> None:
    store = store_module.VendorTrailStore(
        Hass(),
        "mower",
        storage=DiskStorage(tmp_path / "store.json"),
    )
    snapshot, _ = observe(store)
    assert store.accept(geometry(end=2))

    live = session(end=6)
    live["points"][3][6] = 4  # non-cutting action between two mowing fragments
    store.update_live_tail(snapshot, live)

    assert store.live_tail(92) == [
        [[2, 0]],
        [[4, 0], [5, 0], [6, 0]],
    ]


def test_prepared_static_render_ignores_diagnostic_only_georeference_churn() -> None:
    async def check() -> None:
        old_interval = prepared.LIVE_PREPARE_MIN_INTERVAL_SECONDS
        prepared.LIVE_PREPARE_MIN_INTERVAL_SECONDS = 0.0
        try:
            owner = _FakeCoordinator()
            owner.data["map"] = dict(owner.data["map"])
            owner.data["map"]["georeference"] = {
                "schema_version": 1,
                "source": "learned",
                "status": "validated",
                "map_revision": "map-r7",
                "reference": {
                    "local_x": 0.0,
                    "local_y": 0.0,
                    "latitude": 59.0,
                    "longitude": 24.0,
                },
                "rotation_rad": 0.1,
                "local_frame_check": {"sample_count": 4},
                "reference_candidates": {"count": 2},
            }

            manager = prepared.PreparedRenderModelManager(owner)
            manager.start()
            await _wait_tasks(manager)
            first = manager.diagnostics()
            assert first["static_build_count"] == 1
            first_id = first["static_resource_id"]

            owner.data["map"]["georeference"]["local_frame_check"] = {
                "sample_count": 5,
                "offset_m": 0.12,
            }
            owner.data["map"]["georeference"]["reference_candidates"] = {
                "count": 3,
                "latest": "poll",
            }
            manager.request_refresh()
            await _wait_tasks(manager)
            diagnostic_only = manager.diagnostics()
            assert diagnostic_only["static_build_count"] == 1
            assert diagnostic_only["static_resource_id"] == first_id

            owner.data["map"]["georeference"]["reference"]["latitude"] = 59.00001
            manager.request_refresh()
            await _wait_tasks(manager)
            real_change = manager.diagnostics()
            assert real_change["static_build_count"] == 2
            assert real_change["static_resource_id"] != first_id

            await manager.async_shutdown()
        finally:
            prepared.LIVE_PREPARE_MIN_INTERVAL_SECONDS = old_interval

    asyncio.run(check())


def test_beta22_release_metadata() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"].startswith("0.4.5-beta")

    note = RELEASE_NOTE.read_text(encoding="utf-8")
    assert note.startswith("title: Navimower 0.4.5-beta22")
    assert "taskDelay" in note
    assert "MQTT cutting action" in note
    assert "georeference" in note.lower()

    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    assert "_mqtt_task_delay_last_update" in coordinator
    assert "def mqtt_task_delay_age" in coordinator
