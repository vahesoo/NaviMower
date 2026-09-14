"""Regression coverage for Base64(Zstd(JSON)) retained vendor trails."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def test_vendor_trail_decodes_with_packaged_zstandard_fallback() -> None:
    """Exercise the exact transport shape seen from get-path-info-data-compress."""
    code = textwrap.dedent(
        r'''
        import base64
        import importlib.util
        import json
        from pathlib import Path
        import sys
        import types
        import zstandard

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

        expected = [
            {
                "partitionId": 431,
                "partitionPercentage": 66,
                "startTime": 1789364504,
                "endTime": 1789411714,
                "points": [
                    {"x": -50.75, "y": -70.5, "kr": "01", "pid": "431", "pt": "04"},
                    {"x": -50.5, "y": -70.25, "kr": "01", "pid": "431", "pt": "04"},
                ],
            }
        ]
        raw = json.dumps(expected, separators=(",", ":")).encode("utf-8")
        packed = zstandard.ZstdCompressor(level=3).compress(raw)
        encoded = base64.b64encode(packed).decode("ascii")

        # Force the code path that failed on Home Assistant OS: stdlib
        # compression.zstd unavailable, packaged zstandard must decode it.
        sys.modules["compression.zstd"] = None
        decoded = target.decode_vendor_trail_response(encoded)
        assert decoded == expected

        normalized = target.normalize_vendor_trail_row(
            decoded[0],
            coverage={"pct": 66, "start_time": 1789364504, "end_time": 1789411714},
        )
        assert normalized["zone_id"] == 431
        assert normalized["point_count"] == 2
        assert normalized["points"][-1][:2] == [-50.5, -70.25]
        assert normalized["progress"] == 66
        '''
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)
