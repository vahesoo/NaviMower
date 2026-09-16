"""Regression tests for strict vendor-backbone / MQTT-tail semantics."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def test_vendor_tail_semantics() -> None:
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

        coordinator = module("custom_components.navimower.coordinator_semantics")

        def base_trim(segments, vendor_points, *, radius_m=0.75):
            if vendor_points and vendor_points[-1][0] == 999:
                return segments, {
                    "matched": False,
                    "mqtt_tail_point_count": sum(len(item) for item in segments),
                    "mqtt_tail_distance_m": 42.0,
                    "anchor_xy": None,
                }
            return segments, {
                "matched": True,
                "mqtt_tail_point_count": sum(len(item) for item in segments),
                "mqtt_tail_distance_m": 20.0,
                "anchor_xy": [0.0, 0.0],
            }

        coordinator.trim_mqtt_tail_segments = base_trim

        spec = importlib.util.spec_from_file_location(
            "custom_components.navimower.vendor_tail_semantics",
            root / "custom_components" / "navimower" / "vendor_tail_semantics.py",
        )
        target = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = target
        spec.loader.exec_module(target)

        target.install_vendor_tail_semantics()

        long_tail = [[[0.0, 0.0], [5.0, 0.0], [10.0, 0.0], [15.0, 0.0], [20.0, 0.0]]]
        tail, metrics = coordinator.trim_mqtt_tail_segments(
            long_tail,
            [[0.0, 0.0, "", ""], [20.0, 0.0, "", ""]],
        )
        assert metrics["matched"] is True
        assert metrics["tail_limit_m"] == 8.0
        assert metrics["mqtt_tail_distance_m"] == 8.0
        assert len(tail) == 1
        assert tail[0][-1] == [20.0, 0.0]
        assert abs(tail[0][0][0] - 12.0) < 1e-9

        tail, metrics = coordinator.trim_mqtt_tail_segments(
            long_tail,
            [[999.0, 0.0, "", ""]],
        )
        assert tail == []
        assert metrics["matched"] is False
        assert metrics["suppressed_without_vendor_anchor"] is True
        assert metrics["mqtt_tail_point_count"] == 0
        assert metrics["mqtt_tail_distance_m"] == 0.0

        split = [
            [[0.0, 0.0], [100.0, 0.0]],
            [[200.0, 0.0], [204.0, 0.0], [208.0, 0.0], [212.0, 0.0]],
        ]
        tail, metrics = coordinator.trim_mqtt_tail_segments(
            split,
            [[212.0, 0.0, "", ""]],
        )
        assert len(tail) == 1
        assert tail[0][-1] == [212.0, 0.0]
        assert tail[0][0] == [204.0, 0.0]
        assert metrics["mqtt_tail_distance_m"] == 8.0
        '''
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)
