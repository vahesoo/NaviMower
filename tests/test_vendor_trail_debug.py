"""Isolated regression tests for retained-vendor/MQTT-tail debug helpers."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def test_vendor_trail_debug_helpers() -> None:
    code = textwrap.dedent(
        r'''
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
        current.CurrentCycleRenderManager = CurrentCycleRenderManager

        svg = module("custom_components.navimower.session_svg")
        svg.SESSION_SVG_ARCHIVE_VERSION = 2
        svg.build_session_svg_archive = lambda session: None

        spec = importlib.util.spec_from_file_location(
            "custom_components.navimower.vendor_trail",
            root / "custom_components" / "navimower" / "vendor_trail.py",
        )
        target = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = target
        spec.loader.exec_module(target)

        row = target.normalize_vendor_trail_row(
            {
                "partitionId": 42,
                "partitionPercentage": 100,
                "startTime": 100,
                "endTime": 200,
                "points": [{"x": 1.0, "y": 2.0, "kr": "01", "pt": "04"}],
            },
            coverage={"pct": 0, "start_time": 300, "end_time": 0},
        )
        assert row["progress"] == 0
        assert row["start_time"] == 300
        assert row["end_time"] == 0

        snapshot = {
            "current_zone_ids": [42],
            "coverage": {"zones": [{"id": 42, "pct": 0, "start_time": 300}]},
        }
        cache = {
            42: {
                "zone_id": 42,
                "start_time": 300,
                "progress": 0,
                "points": [[1.0, 2.0, "01", "04"]],
            }
        }
        assert target.current_vendor_rows(snapshot, cache) == []

        public_payload = {
            "active_session": {"zone_ids": [91, 92]},
            "coverage": {
                "zones": [
                    {"id": 91, "pct": 100, "start_time": 100},
                    {"id": 92, "pct": 76, "start_time": 100},
                ]
            },
            "zone_states": [
                {"id": 91, "active": False},
                {"id": 92, "active": True},
            ],
        }
        public_cache = {
            91: {"zone_id": 91, "start_time": 100, "progress": 100, "points": [[1.0, 1.0, "01", "04"]]},
            92: {"zone_id": 92, "start_time": 100, "progress": 76, "points": [[2.0, 2.0, "01", "04"]]},
        }
        assert target.active_vendor_row(public_payload, public_cache)["zone_id"] == 92

        mqtt = [[[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0], [4.0, 0.0]]]
        tail, metrics = target.trim_mqtt_tail_segments(
            mqtt,
            [[0.0, 0.0, "01", "04"], [2.05, 0.0, "01", "04"]],
            radius_m=0.2,
        )
        assert metrics["matched"] is True
        assert tail == [[[2.0, 0.0], [3.0, 0.0], [4.0, 0.0]]]
        assert metrics["mqtt_tail_point_count"] == 3

        repeated = [[[0.0, 0.0], [1.0, 0.0], [0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]]
        tail, metrics = target.trim_mqtt_tail_segments(
            repeated,
            [[0.0, 0.0, "01", "04"]],
            radius_m=0.1,
        )
        assert metrics["matched"] is True
        assert tail == [[[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]]

        source = target.build_vendor_render_source(
            [
                {"zone_id": 5, "start_time": 100, "points": [[0, 0, "01", "04"], [1, 0, "01", "04"]]},
                {"zone_id": 91, "start_time": 200, "points": [[5, 5, "01", "04"], [6, 5, "01", "04"]]},
            ],
            mowing_path_width_m=0.25,
        )
        assert source["active"] is False
        assert len(source["segment_starts_ms"]) == 2
        assert [point[7] for point in source["points"]] == [5, 5, 91, 91]
        assert all(point[6] == 5 for point in source["points"])
        '''
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)
