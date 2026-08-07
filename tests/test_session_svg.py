"""Isolated regression test for completed-session SVG render artifacts."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def test_completed_session_svg_archive() -> None:
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
        const.ACTIVITY_DOCKED = "docked"
        const.ACTIVITY_ERROR = "error"
        const.ACTIVITY_MOWING = "mowing"
        const.ACTIVITY_PAUSED = "paused"
        const.ACTIVITY_RETURNING = "returning"
        const.MAP_CARD_MIN_POINT_DISTANCE_M = 0.30
        const.MQTT_CUTTING_ACTIONS = {5, 8}
        const.SWATH_WIDTH_M = 0.25
        zone_state = module("custom_components.navimower.zone_state")

        def simplify(points, *, min_distance_m=0.30):
            if len(points) <= 2:
                return [list(point) for point in points]
            result = [list(points[0])]
            threshold = min_distance_m * min_distance_m
            for point in points[1:-1]:
                if (point[0] - result[-1][0]) ** 2 + (point[1] - result[-1][1]) ** 2 >= threshold:
                    result.append(list(point))
            result.append(list(points[-1]))
            return result

        zone_state.simplify_xy_points = simplify
        spec = importlib.util.spec_from_file_location(
            "custom_components.navimower.session_svg",
            root / "custom_components" / "navimower" / "session_svg.py",
        )
        target = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = target
        spec.loader.exec_module(target)

        session = {
            "id": "test-1",
            "active": False,
            "ended_at_ms": 20_000,
            "segment_starts_ms": [1_000],
            "points": [
                [1_000, 0.0, 0.0, 0.0, "returning", 5, 2, None],
                [2_000, 1.0, 0.0, 0.0, "mowing", 4, 5, 1],
                [3_000, 2.0, 0.0, 0.0, "mowing", 4, 5, 1],
                [4_000, 3.0, 0.0, 0.0, "mowing", 4, 5, 1],
                [5_000, 4.0, 0.0, 0.0, "returning", 5, 2, None],
            ],
        }
        artifact = target.build_session_svg_archive(session)
        assert artifact is not None
        assert artifact["version"] == 2
        assert artifact["coordinate_space"] == "map_xy_m"
        assert artifact["source"]["point_count"] == 5
        assert artifact["mowed_area"]["path_d"].startswith("M")
        assert artifact["mowed_area"]["fill_rule"] == "evenodd"
        assert artifact["mowed_area"]["swath_width_m"] == 0.25
        assert artifact["mowed_area"]["loop_count"] >= 1
        assert artifact["travel"]["path_d"].startswith("M")
        assert artifact["route"]["path_d"].startswith("M")
        assert target.render_matches_session(artifact, session)
        assert not target.render_matches_session(artifact, {**session, "ended_at_ms": 21_000})
        assert target.build_session_svg_archive({**session, "active": True}) is None

        separate = {
            "id": "test-2",
            "active": False,
            "ended_at_ms": 9_000,
            "segment_starts_ms": [1_000, 5_000],
            "points": [
                [1_000, 0.0, 0.0, 0.0, "mowing", 4, 5, 1],
                [2_000, 3.0, 0.0, 0.0, "mowing", 4, 5, 1],
                [5_000, 0.0, 1.0, 0.0, "mowing", 4, 5, 1],
                [6_000, 3.0, 1.0, 0.0, "mowing", 4, 5, 1],
            ],
        }
        separate_artifact = target.build_session_svg_archive(separate)
        assert separate_artifact["mowed_area"]["loop_count"] >= 2
        '''
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)
