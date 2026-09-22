"""Regression coverage for 0.4.5-beta25 prepared live-route efficiency."""
from __future__ import annotations

import asyncio
from pathlib import Path

from test_prepared_render_model_beta21 import (
    _FakeCoordinator,
    _wait_tasks,
    prepared,
)

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta25_live_prepare_cadence_and_short_tail_contract() -> None:
    assert prepared.LIVE_PREPARE_MIN_INTERVAL_SECONDS == 30.0
    assert prepared.LIVE_TAIL_MAX_POINTS == 128

    async def check() -> None:
        owner = _FakeCoordinator()
        owner.live_segments = [[[0, 0], [1, 0], [2, 0]]]
        manager = prepared.PreparedRenderModelManager(owner)
        manager.start()
        await _wait_tasks(manager)

        first = manager.diagnostics()
        assert first["live_build_count"] == 1
        assert first["live_prepare_min_interval_s"] == 30.0
        assert first["live_tail_max_points"] == 128
        resource_id = first["live_resource_id"]

        tail = manager.live_tail_payload(
            [[[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]]],
            owner.history.active_session_no,
        )
        assert tail["usable"] is True
        assert tail["base_resource_id"] == resource_id
        assert tail["base_point_count"] == 3
        assert tail["current_point_count"] == 5
        assert tail["point_count"] == 3
        assert tail["segment_count"] == 1
        assert tail["segments"] == [[[2.0, 0.0], [3.0, 0.0], [4.0, 0.0]]]

        unchanged = manager.live_tail_payload(
            [[[0, 0], [1, 0], [2, 0]]],
            owner.history.active_session_no,
        )
        assert unchanged["usable"] is True
        assert unchanged["point_count"] == 0
        assert unchanged["segments"] == []

        mismatch = manager.live_tail_payload(
            [[[0, 0], [1, 0], [2, 0], [3, 0]]],
            owner.history.active_session_no + 1,
        )
        assert mismatch["usable"] is False
        assert mismatch["reason"] == "trail_session_mismatch"

        long_segment = [[float(index), 0.0] for index in range(140)]
        limited = manager.live_tail_payload(
            [long_segment],
            owner.history.active_session_no,
        )
        assert limited["usable"] is False
        assert limited["reason"] == "tail_limit_exceeded"

        resource = manager.resource("live", resource_id)
        assert resource is not None
        manager.record_resource_response(
            "live",
            byte_length=len(resource["body"]),
        )
        manager.record_resource_response("live", not_modified=True)
        metrics = manager.diagnostics()
        assert metrics["live_resource_reads"] == 1
        assert metrics["live_resource_bytes_served_total"] == len(resource["body"])
        assert metrics["live_resource_not_modified_count"] == 1
        assert metrics["live_resource_first_read_age_s"] is not None
        assert metrics["live_resource_last_read_age_s"] is not None
        assert metrics["live_tail_requests"] == 4
        assert metrics["live_tail_success_count"] == 2
        assert metrics["live_tail_full_fallback_count"] == 2
        assert metrics["live_tail_max_points_observed"] > 128

        discovery = manager.discovery()
        assert discovery["live_route_min_interval_s"] == 30.0
        assert discovery["live_tail_max_points"] == 128
        assert discovery["capabilities"]["live_route_short_tail"] is True
        assert discovery["capabilities"]["live_route_tail_only_query"] is True
        manifest = manager.manifest()
        assert manifest["live_route_min_interval_s"] == 30.0
        assert manifest["live_tail_max_points"] == 128
        await manager.async_shutdown()

    asyncio.run(check())


def test_beta25_noncritical_churn_waits_but_zone_transition_publishes_now() -> None:
    async def check() -> None:
        owner = _FakeCoordinator()
        owner.live_segments = [[[0, 0], [1, 0], [2, 0]]]
        manager = prepared.PreparedRenderModelManager(owner)
        manager.start()
        await _wait_tasks(manager)
        assert manager.live_build_count == 1

        # Normal active-trail growth is intentionally coalesced behind the
        # 30-second prepared backbone. The frontend's raw/MQTT tail remains live.
        owner.history.trail_revision += 1
        owner.vendor_trail_store.revision += 1
        owner.live_segments = [[[0, 0], [1, 0], [2, 0], [3, 0]]]
        manager.request_refresh()
        await asyncio.sleep(0.05)
        assert manager.live_build_count == 1
        assert manager._live_timer is not None

        # Session/activity/zone/trail-active transitions bypass the cadence so
        # the prepared backbone cannot remain attached to a stale lifecycle.
        owner.data["current_physical_zone_id"] = 91
        manager.request_refresh()
        await _wait_tasks(manager)
        assert manager.live_build_count == 2
        assert manager._live_timer is None
        assert manager.diagnostics()["live_build_count"] == 2

        await manager.async_shutdown()

    asyncio.run(check())


def test_beta25_map_api_has_safe_tail_only_opt_in() -> None:
    api = (COMPONENT / "map_api_performance.py").read_text(encoding="utf-8")

    assert '"prepared_live_tail_only"' in api
    assert '"prepared_live_tail"' in api
    assert "prepared.live_tail_payload(" in api
    assert 'payload.pop("trail", None)' in api
    assert 'payload.pop("trail_segments", None)' in api
    assert 'prepared_live_tail_only and live_tail.get("usable")' in api
    assert "manager.record_resource_response(kind, not_modified=True)" in api
    assert 'manager.record_resource_response(kind, byte_length=len(resource["body"]))' in api
