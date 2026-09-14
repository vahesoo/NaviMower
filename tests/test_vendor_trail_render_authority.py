"""Regression coverage for vendor/current-cycle source arbitration."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def test_vendor_geometry_replaces_mqtt_for_same_zone_only() -> None:
    code = textwrap.dedent(
        r'''
        import asyncio
        import importlib.util
        from pathlib import Path
        import sys
        import types

        root = Path.cwd()

        def module(name):
            value = types.ModuleType(name)
            sys.modules[name] = value
            return value

        module("custom_components")
        navimower = module("custom_components.navimower")
        navimower.__path__ = [str(root / "custom_components" / "navimower")]

        const = module("custom_components.navimower.const")
        const.SWATH_WIDTH_M = 0.25

        current = module("custom_components.navimower.current_cycle_render")
        class CurrentCycleRenderManager:
            def __init__(self, coordinator):
                self.coordinator = coordinator
                self.history = coordinator.history

            async def async_get(self, map_zones):
                return {
                    "scope": "current_cycle",
                    "revision": "mqtt-base",
                    "coordinate_space": "map_xy_m",
                    "zone_ids": [431, 436],
                    "zones": [],
                    "source_point_count": 4,
                    "mowed_area": {"path_d": "BASE", "fill_rule": "evenodd"},
                }

        current.CurrentCycleRenderManager = CurrentCycleRenderManager
        current.build_current_cycle_render_source = lambda sessions, zones: {}

        svg = module("custom_components.navimower.session_svg")
        svg.SESSION_SVG_ARCHIVE_VERSION = 2
        def build_archive(source):
            if source.get("id") == "vendor":
                return {"version": 2, "mowed_area": {"path_d": "V", "fill_rule": "evenodd"}}
            return {"version": 2, "mowed_area": {"path_d": "F", "fill_rule": "evenodd"}}
        svg.build_session_svg_archive = build_archive

        zone_state = module("custom_components.navimower.zone_state")
        zone_state.as_int = lambda value: int(value) if value is not None else None
        zone_state.as_float = lambda value: float(value) if value is not None else None
        zone_state.zone_id_for_point = lambda x, y, zones: None

        vendor = module("custom_components.navimower.vendor_trail")
        class VendorTrailCurrentCycleRenderManager(CurrentCycleRenderManager):
            def __init__(self, coordinator):
                super().__init__(coordinator)
                self._vendor_revision = None
                self._vendor_artifact = None
        vendor.VendorTrailCurrentCycleRenderManager = VendorTrailCurrentCycleRenderManager
        vendor.current_vendor_rows = lambda snapshot, cache: [
            {"zone_id": 431, "point_count": 2, "points": [[0, 0], [1, 0]]}
        ]
        vendor.vendor_rows_revision = lambda rows: "vendor-rev"
        vendor.build_vendor_render_source = lambda rows, mowing_path_width_m: {
            "id": "vendor",
            "active": False,
            "points": [[1, 0, 0], [2, 1, 0]],
        }

        spec = importlib.util.spec_from_file_location(
            "custom_components.navimower.vendor_trail_render_semantics",
            root / "custom_components" / "navimower" / "vendor_trail_render_semantics.py",
        )
        target = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = target
        spec.loader.exec_module(target)

        source = {
            "id": "current-cycle",
            "active": False,
            "points": [
                [1, 0.0, 0.0, 0.0, "mowing", 4, 5, 431],
                [2, 1.0, 0.0, 0.0, "mowing", 4, 5, 431],
                [3, 5.0, 0.0, 0.0, "mowing", 4, 5, 436],
                [4, 6.0, 0.0, 0.0, "mowing", 4, 5, 436],
            ],
            "segment_starts_ms": [1, 3],
            "zone_ids": [431, 436],
            "current_cycle_zones": [
                {"zone_id": 431, "point_count": 2},
                {"zone_id": 436, "point_count": 2},
            ],
        }
        filtered = target.filter_current_cycle_source(source, {431}, [])
        assert [point[7] for point in filtered["points"]] == [436, 436]
        assert filtered["segment_starts_ms"] == [3]
        assert filtered["zone_ids"] == [436]

        class History:
            def session_summaries(self, include_points=False):
                return []

        class Hass:
            async def async_add_executor_job(self, func, *args):
                return func(*args)

        class Coordinator:
            data = {"mowing_path_width_m": 0.25}
            history = History()
            hass = Hass()
            _vendor_trail_cache = {431: {"zone_id": 431}}

        manager = VendorTrailCurrentCycleRenderManager(Coordinator())

        async def fake_current_cycle_source(manager, map_zones):
            return source
        target._current_cycle_source = fake_current_cycle_source

        result = asyncio.run(target._authoritative_async_get(manager, []))
        assert result["mowed_area"]["path_d"] == "FV"
        assert "BASE" not in result["mowed_area"]["path_d"]
        assert result["source"] == "vendor_retained_per_zone_with_mqtt_fallback"
        assert result["vendor_trail_debug"]["mqtt_base_suppressed_for_zone_ids"] == [431]
        assert result["vendor_trail_debug"]["mqtt_fallback_zone_ids"] == [436]
        '''
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)
