"""Regression coverage for 0.4.5-beta27 semantic live/travel preparation."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta27_strict_zone_classifier_and_semantic_live_models() -> None:
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

        svg_spec = importlib.util.spec_from_file_location(
            "custom_components.navimower.session_svg",
            root / "custom_components" / "navimower" / "session_svg.py",
        )
        svg = importlib.util.module_from_spec(svg_spec)
        sys.modules[svg_spec.name] = svg
        svg_spec.loader.exec_module(svg)

        assert svg.SESSION_SVG_ARCHIVE_VERSION == 2
        assert svg.SESSION_SVG_CLASSIFIER_VERSION == 2

        session = {
            "id": "semantic-live",
            "active": True,
            "started_at_ms": 1_000,
            "segment_starts_ms": [1_000],
            "points": [
                [1_000, 0.0, 0.0, 0.0, "mowing", 4, 5, 1],
                [2_000, 1.0, 0.0, 0.0, "mowing", 4, 5, 1],
                # Blade remains on but the edge crosses into a different zone:
                # the boundary edge must be travel, never a widened mow swath.
                [3_000, 2.0, 0.0, 0.0, "mowing", 4, 5, 2],
                [4_000, 3.0, 0.0, 0.0, "mowing", 4, 5, 2],
                # Outside any mowing zone: even blade-on samples stay travel.
                [5_000, 4.0, 0.0, 0.0, "mowing", 4, 5, None],
                [6_000, 5.0, 0.0, 0.0, "mowing", 4, 5, None],
                # Explicit transit inside a zone also stays travel.
                [7_000, 6.0, 0.0, 0.0, "returning", 5, 2, 2],
            ],
        }
        all_segments, cutting, travel = svg.split_session_route_segments(session)
        assert len(all_segments) == 1
        assert cutting == [
            [[0.0, 0.0], [1.0, 0.0]],
            [[2.0, 0.0], [3.0, 0.0]],
        ]
        assert travel, "zone-boundary/outside movement must be retained as travel"
        # Strong semantic assertions without depending on segment coalescing
        # details: no cutting edge may cross zones or use a missing zone id.
        cutting_edges = {
            (tuple(seg[i]), tuple(seg[i + 1]))
            for seg in cutting
            for i in range(len(seg) - 1)
        }
        assert ((1.0, 0.0), (2.0, 0.0)) not in cutting_edges
        assert ((3.0, 0.0), (4.0, 0.0)) not in cutting_edges
        assert ((4.0, 0.0), (5.0, 0.0)) not in cutting_edges

        completed = {**session, "active": False, "ended_at_ms": 7_000}
        artifact = svg.build_session_svg_archive(completed)
        assert artifact is not None
        assert artifact["version"] == 2
        assert artifact["source"]["classifier_version"] == 2
        assert svg.render_matches_session(artifact, completed)
        old = {**artifact, "source": dict(artifact["source"])}
        old["source"].pop("classifier_version")
        assert not svg.render_matches_session(old, completed)

        custom_area = module("custom_components.navimower.custom_area")
        custom_area.OPT_CUSTOM_AREAS = "custom_areas"
        custom_area.parse_custom_areas = lambda _value: []

        prep_spec = importlib.util.spec_from_file_location(
            "custom_components.navimower.prepared_render_model",
            root / "custom_components" / "navimower" / "prepared_render_model.py",
        )
        prep = importlib.util.module_from_spec(prep_spec)
        sys.modules[prep_spec.name] = prep
        prep_spec.loader.exec_module(prep)

        model = prep.build_live_route_render_model(
            {
                "trail_segments": [[[0.0, 0.0], [6.0, 0.0]]],
                "trail_session": 10,
                "trail_active": True,
                "activity": "mowing",
                "current_physical_zone_id": 2,
                "active_session": session,
            }
        )
        assert model["segment_count"] == 1
        semantic = model["semantic_route"]
        assert semantic["available"] is True
        assert semantic["session_id"] == "semantic-live"
        assert semantic["source_point_count"] == 7
        assert semantic["cutting_segment_count"] == 2
        assert semantic["travel_segment_count"] >= 1
        assert all(row["kind"] == "cutting" for row in semantic["cutting_segments"])
        assert all(row["kind"] == "travel" for row in semantic["travel_segments"])

        tail = prep._semantic_tail_model(
            session,
            base_session_id="semantic-live",
            base_point_count=3,
        )
        assert tail["usable"] is True
        assert tail["base_point_count"] == 3
        assert tail["current_point_count"] == 7
        assert tail["point_count"] == 5  # one overlap point + four new points
        assert tail["travel_segment_count"] >= 1

        mismatch = prep._semantic_tail_model(
            session,
            base_session_id="another-session",
            base_point_count=3,
        )
        assert mismatch["usable"] is False
        assert mismatch["reason"] == "semantic_session_mismatch"
        '''
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)


def test_beta27_backend_contract_is_additive_for_beta15() -> None:
    prepared = (COMPONENT / "prepared_render_model.py").read_text(encoding="utf-8")
    performance = (COMPONENT / "map_api_performance.py").read_text(encoding="utf-8")
    svg = (COMPONENT / "session_svg.py").read_text(encoding="utf-8")

    assert '"live_route_semantic_segments": True' in prepared
    assert '"live_semantic_route_resource": True' in prepared
    assert '"live_tail_semantic_segments": True' in prepared
    assert '"live_semantic_tail_query": True' in prepared
    assert '"live_semantic_tail_only_query": True' in prepared
    assert '"mowed_edge_requires_same_zone": True' in prepared
    assert '"semantic_route": semantic' in prepared
    assert '"scope": "live_semantic_route_render_model"' in prepared
    assert "**semantic" in prepared
    assert '"active_session": deepcopy(active_session)' in prepared

    # Keep the beta15 all-movement fields intact until the frontend migration.
    assert '"segments": rows' in prepared
    assert '"segments": deepcopy(tail)' in prepared
    assert 'legacy_model.pop("semantic_route", None)' in prepared
    assert '"live_semantic_route": deepcopy(live_semantic)' in prepared
    assert '"live_route_short_tail": True' in prepared
    assert '"live_route_tail_only_query": True' in prepared

    # beta15 does not request or serialize semantic geometry; the future
    # frontend must opt into both the resource and short-tail query.
    assert 'if "live_semantic_route_render" in request.query:' in performance
    assert 'query_key="live_semantic_route_render"' in performance
    assert '"prepared_live_semantic_tail"' in performance
    assert '"prepared_live_semantic_tail_only"' in performance
    assert 'payload["prepared_live_semantic_tail"] = semantic_tail' in performance
    assert "include_prepared_semantic_live_tail" in performance
    assert "prepared_live_semantic_tail_only" in performance
    assert "semantic_live_tail_payload()" in performance
    assert "def semantic_live_tail_payload" in prepared

    assert "SESSION_SVG_ARCHIVE_VERSION = 2" in svg
    assert "SESSION_SVG_CLASSIFIER_VERSION = 2" in svg
    assert "previous[4] == current_point[4]" in svg
