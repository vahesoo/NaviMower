"""Regression coverage for 0.4.5-beta24 unowned fallback checkpoints."""
from __future__ import annotations

import asyncio
from copy import deepcopy

from test_map_artifacts_beta14 import checkpoint, owner_for, seed
from test_vendor_trail_store import ZONES, session, store


def _append_history_point(history, index: int = -1) -> None:
    row = history.sessions[index]
    point = list(row["points"][-1])
    point[0] += 1000
    point[1] += 1
    row["points"].append(point)
    row["point_count"] = len(row["points"])
    history.trail_revision += 1


def test_unowned_history_growth_stays_off_map_artifact_hot_path(store) -> None:
    """Live History churn must not rebuild a frozen mixed vendor/fallback base."""
    seed(store, both=False)  # zone 92 vendor-owned; zone 91 remains History fallback
    history = [
        session("unowned-base", zone=91, end=5, active=False),
        session("owned-live", zone=92, end=5, stamp=2_000_000_000_000, active=True),
    ]
    owner = owner_for(store, sessions=history)

    async def check() -> None:
        manager = owner.map_artifacts
        await checkpoint(manager, reason="startup")
        baseline = await manager.async_get(ZONES)
        assert baseline["vendor_trail_debug"]["mqtt_fallback_zone_ids"] == [91]
        assert manager.manifest()["fallback_zone_ids"] == [91]

        build_count = manager.build_count
        svg_builds = owner.hass.svg_builds
        frozen_path = baseline["mowed_area"]["path_d"]

        for _ in range(50):
            _append_history_point(owner.history, -1)
            task = manager.request_refresh()
            if task is not None:
                await task
            current = await manager.async_get(ZONES)
            assert current["mowed_area"]["path_d"] == frozen_path

        assert manager.build_count == build_count
        assert owner.hass.svg_builds == svg_builds
        diagnostics = manager.diagnostics()
        assert diagnostics["fallback_checkpoint_revision"] == 0
        assert diagnostics["fallback_checkpoint_count"] == 0
        assert diagnostics["current_history_revision"] == 51
        assert diagnostics["fallback_zone_count"] == 1

        # First observation only establishes lifecycle baseline.
        manager.observe({
            "activity": "mowing",
            "current_physical_zone_id": 91,
        })
        # Leaving the unowned active zone is the explicit fallback checkpoint.
        manager.observe({
            "activity": "mowing",
            "current_physical_zone_id": 92,
        })
        if manager._task is not None:
            await manager._task

        refreshed = await manager.async_get(ZONES)
        assert refreshed["mowed_area"]["path_d"] == frozen_path
        assert manager.build_count == build_count + 1
        assert owner.hass.svg_builds == svg_builds + 1

        diagnostics = manager.diagnostics()
        assert diagnostics["fallback_checkpoint_revision"] == 1
        assert diagnostics["fallback_checkpoint_count"] == 1
        assert diagnostics["last_fallback_checkpoint_reason"] == "zone_exit"
        assert diagnostics["last_fallback_checkpoint_history_revision"] == 51
        assert diagnostics["current_history_revision"] == 51

        await manager.async_shutdown()

    asyncio.run(check())


def test_direct_current_cycle_reads_share_frozen_fallback_checkpoint(store) -> None:
    """Snapshot/direct consumers must not bypass the fallback checkpoint cache."""
    seed(store, both=False)
    history = [
        session("unowned-base", zone=91, end=5, active=False),
        session("owned-live", zone=92, end=5, stamp=2_000_000_000_000, active=True),
    ]
    owner = owner_for(store, sessions=history)

    async def check() -> None:
        manager = owner.map_artifacts
        await checkpoint(manager, reason="startup")
        baseline = await owner.current_cycle_render_manager.async_get(ZONES)
        frozen_path = baseline["mowed_area"]["path_d"]
        svg_builds = owner.hass.svg_builds

        for _ in range(20):
            _append_history_point(owner.history, -1)
            direct = await owner.current_cycle_render_manager.async_get(ZONES)
            assert direct["mowed_area"]["path_d"] == frozen_path

        assert owner.hass.svg_builds == svg_builds

        task = manager.request_fallback_checkpoint(reason="session_settled")
        if task is not None:
            await task

        direct = await owner.current_cycle_render_manager.async_get(ZONES)
        assert direct["mowed_area"]["path_d"] == frozen_path
        assert owner.hass.svg_builds == svg_builds + 1
        assert (
            direct["vendor_trail_debug"]["fallback_checkpoint_revision"]
            == manager.fallback_checkpoint_revision
            == 1
        )

        await manager.async_shutdown()

    asyncio.run(check())


def test_completed_unowned_session_is_added_only_at_settled_checkpoint(store) -> None:
    """Active unowned points stay live-only until the session becomes completed."""
    seed(store, both=False)
    history = [
        session("unowned-base", zone=91, end=5, active=False),
        session(
            "unowned-live",
            zone=91,
            start=6,
            end=9,
            stamp=2_000_000_000_000,
            active=True,
        ),
    ]
    owner = owner_for(store, sessions=history)

    async def check() -> None:
        manager = owner.map_artifacts
        await checkpoint(manager, reason="startup")
        baseline = await manager.async_get(ZONES)
        frozen_path = baseline["mowed_area"]["path_d"]
        svg_builds = owner.hass.svg_builds

        for _ in range(20):
            _append_history_point(owner.history, -1)
            direct = await owner.current_cycle_render_manager.async_get(ZONES)
            assert direct["mowed_area"]["path_d"] == frozen_path

        # History closes the session before the settled mower observation.
        owner.history.sessions[-1]["active"] = False
        owner.history.trail_revision += 1

        manager.observe({
            "activity": "mowing",
            "current_physical_zone_id": 91,
        })
        manager.observe({
            "activity": "docked",
            "current_physical_zone_id": None,
        })
        if manager._task is not None:
            await manager._task

        refreshed = await manager.async_get(ZONES)
        assert refreshed["mowed_area"]["path_d"] != frozen_path
        assert owner.hass.svg_builds == svg_builds + 1

        diagnostics = manager.diagnostics()
        assert diagnostics["fallback_checkpoint_count"] == 1
        assert diagnostics["last_fallback_checkpoint_reason"] == "session_settled"
        await manager.async_shutdown()

    asyncio.run(check())


def test_zone_exit_and_settled_transition_coalesce_to_one_fallback_checkpoint(store) -> None:
    """Mowing -> docked with a zone exit must bump the fallback epoch only once."""
    seed(store, both=False)
    owner = owner_for(
        store,
        sessions=[session("unowned-live", zone=91, end=5, active=True)],
    )

    async def check() -> None:
        manager = owner.map_artifacts
        await checkpoint(manager, reason="startup")
        manager.observe({
            "activity": "mowing",
            "current_physical_zone_id": 91,
        })
        manager.observe({
            "activity": "docked",
            "current_physical_zone_id": None,
        })
        if manager._task is not None:
            await manager._task

        diagnostics = manager.diagnostics()
        assert diagnostics["fallback_checkpoint_revision"] == 1
        assert diagnostics["fallback_checkpoint_count"] == 1
        assert diagnostics["last_fallback_checkpoint_reason"] == "session_settled"
        await manager.async_shutdown()

    asyncio.run(check())
