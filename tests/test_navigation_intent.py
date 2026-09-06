"""Dependency-free regression checks for navigation, gate and cycle safety."""
from __future__ import annotations

import ast
import math
from pathlib import Path
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
COORDINATOR = COMPONENT / "coordinator.py"
NAVIGATION = COMPONENT / "navigation_intent.py"
POSITION = COMPONENT / "position_fallback.py"
HISTORY_PERFORMANCE = COMPONENT / "history_performance.py"
MAP_API_PERFORMANCE = COMPONENT / "map_api_performance.py"
RUNTIME = COMPONENT / "runtime.py"
SCHEDULE = COMPONENT / "navimower_schedule.py"
SERVICES = COMPONENT / "services.py"
MOWER = COMPONENT / "lawn_mower.py"


def load_functions(
    path: Path,
    names: set[str],
    namespace: dict[str, Any],
) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, path.name, "exec"), namespace)
    return namespace


def test_navigation_target_precedence_contract() -> None:
    namespace = load_functions(
        COORDINATOR,
        {"_as_int", "_dedupe_zone_ids", "_resolve_navigation_target_ids"},
        {"Any": Any},
    )
    resolve = namespace["_resolve_navigation_target_ids"]
    common = dict(
        is_docked=False,
        is_returning=False,
        dock_zone_id=13,
        physical_zone_id=24,
        command_target_ids=[],
        command_target_fresh=False,
        mqtt_work_target=None,
        cloud_work_target=None,
        mqtt_partition_ids=[],
        cloud_zone_ids=[],
        last_target_ids=[],
    )

    result = resolve(
        **{
            **common,
            "command_target_ids": [13],
            "command_target_fresh": True,
            "cloud_work_target": 24,
            "cloud_zone_ids": [13],
        }
    )
    assert result[:2] == ([13], "ha_command")

    result = resolve(
        **{**common, "cloud_work_target": 24, "cloud_zone_ids": [13]}
    )
    assert result[:2] == ([13], "private_current_zones")

    result = resolve(
        **{
            **common,
            "is_returning": True,
            "command_target_ids": [24],
            "command_target_fresh": True,
        }
    )
    assert result[:2] == ([13], "returning_to_dock")

    result = resolve(
        **{
            **common,
            "physical_zone_id": 13,
            "command_target_ids": [13],
            "command_target_fresh": True,
            "mqtt_work_target": 13,
        }
    )
    assert result == ([13], "ha_command_confirmed", True)


def test_cloud_timestamp_validation() -> None:
    namespace = load_functions(
        POSITION,
        {"_as_float", "cloud_report_age", "choose_position"},
        {
            "Any": Any,
            "math": math,
            "time": time,
            "CLOUD_GATE_FRESH_SECONDS": 30.0,
            "CLOUD_CLOCK_FUTURE_TOLERANCE_SECONDS": 30.0,
        },
    )
    age = namespace["cloud_report_age"]
    choose = namespace["choose_position"]

    now = 1_800_000_000.0
    assert age(now - 12, now_epoch=now) == 12
    assert age(now + 15, now_epoch=now) == 0
    assert age(now + 86_400, now_epoch=now) is None
    assert age(float("nan"), now_epoch=now) is None
    assert age(float("inf"), now_epoch=now) is None
    assert age(now, now_epoch=float("nan")) is None

    cloud = choose(
        mqtt_position=None,
        mqtt_age=None,
        cloud_position={"x": 1.0, "y": 2.0},
        cloud_report_time=now - 31,
        now_epoch=now,
    )
    assert cloud["source"] == "private_cloud"
    assert cloud["gate_usable"] is False
    assert cloud["stale"] is True

    mqtt = choose(
        mqtt_position={"x": 3.0, "y": 4.0},
        mqtt_age=2.0,
        cloud_position={"x": 1.0, "y": 2.0},
        cloud_report_time=now - 500,
        now_epoch=now,
    )
    assert mqtt["source"] == "mqtt"
    assert mqtt["gate_usable"] is True


def test_target_field_freshness_and_strict_cloud_confirmations() -> None:
    namespace = load_functions(
        NAVIGATION,
        {
            "_as_int",
            "_as_float",
            "_age_seconds",
            "_field_is_fresh",
            "_report_seconds",
            "_advance_confirmation",
        },
        {
            "Any": Any,
            "math": math,
            "time": time,
            "MQTT_STATE_STALE_SECONDS": 90,
        },
    )
    fresh = namespace["_field_is_fresh"]
    advance = namespace["_advance_confirmation"]

    assert fresh(100.0, now=110.0)
    assert not fresh(100.0, now=191.0)
    assert not fresh(111.0, now=110.0)
    assert not fresh(float("nan"), now=110.0)

    first = advance(None, zone_id=37, report_seconds=100.0)
    duplicate = advance(first, zone_id=37, report_seconds=100.0)
    older = advance(duplicate, zone_id=37, report_seconds=99.0)
    second = advance(older, zone_id=37, report_seconds=101.0)
    changed_zone = advance(second, zone_id=36, report_seconds=102.0)

    assert first["count"] == 1
    assert duplicate["count"] == 1
    assert older["count"] == 1
    assert older["report_seconds"] == 100.0
    assert second["count"] == 2
    assert changed_zone["count"] == 1


def test_same_zone_guard_clears_only_fresh_pre_transit_latches() -> None:
    namespace = load_functions(
        NAVIGATION,
        {
            "_as_int",
            "_mapped_channel_active",
            "_latch_conflicts_with_zone",
            "_clear_conflicting_latches",
            "_physical_zone_is_fresh",
        },
        {"Any": Any},
    )
    conflicts = namespace["_latch_conflicts_with_zone"]
    clear = namespace["_clear_conflicting_latches"]
    physical_fresh = namespace["_physical_zone_is_fresh"]

    assert conflicts({"from_zone_id": 36, "to_zone_id": 37}, 36)
    assert not conflicts(
        {"from_zone_id": 36, "to_zone_id": 37, "release_at": 123.0},
        36,
    )
    assert physical_fresh(
        {
            "current_physical_zone_id": 36,
            "current_physical_zone_stale": False,
            "current_physical_zone_position_source": "mqtt",
        },
        36,
    )
    assert not physical_fresh(
        {
            "current_physical_zone_id": 36,
            "current_physical_zone_stale": True,
            "current_physical_zone_position_source": "last_known",
        },
        36,
    )

    class FakeCoordinator:
        def __init__(self) -> None:
            self.data = {
                "current_channel_id": None,
                "current_channel_connection": [],
                "current_channel_stale": False,
            }
            self._gate_latches = {
                "stale": {"from_zone_id": 36, "to_zone_id": 37},
                "arrived": {
                    "from_zone_id": 36,
                    "to_zone_id": 37,
                    "release_at": 500.0,
                },
            }
            self.cancelled: list[str] = []

        def _cancel_gate_release(self, slug: str) -> None:
            self.cancelled.append(slug)

    fake = FakeCoordinator()
    assert clear(fake, 36) == ["stale"]
    assert "arrived" in fake._gate_latches
    assert fake.cancelled == ["stale"]

    channel = FakeCoordinator()
    channel.data = {
        "current_channel_id": 1,
        "current_channel_connection": [36, 37],
        "current_channel_stale": False,
    }
    assert clear(channel, 36) == []
    assert "stale" in channel._gate_latches


def test_explicit_cycle_reset_uses_new_requested_zones() -> None:
    namespace = load_functions(
        HISTORY_PERFORMANCE,
        {"_unique_ints", "_cycle_zone_sets"},
        {"Any": Any},
    )
    zone_sets = namespace["_cycle_zone_sets"]

    assert zone_sets([36], [37]) == ([36], [37])
    assert zone_sets([36], []) == ([36], [36])
    assert zone_sets([], [37]) == ([37], [37])
    assert zone_sets([36, 36], [37, 37]) == ([36], [37])


def test_runtime_wiring_and_performance_contracts() -> None:
    navigation = NAVIGATION.read_text(encoding="utf-8")
    history = HISTORY_PERFORMANCE.read_text(encoding="utf-8")
    map_api = MAP_API_PERFORMANCE.read_text(encoding="utf-8")
    runtime = RUNTIME.read_text(encoding="utf-8")
    schedule = SCHEDULE.read_text(encoding="utf-8")
    services = SERVICES.read_text(encoding="utf-8")
    mower = MOWER.read_text(encoding="utf-8")

    expected = [
        "install_navigation_fallback()",
        "install_navigation_intent()",
        "install_history_performance()",
        "install_map_api_performance()",
    ]
    positions = [runtime.index(item) for item in expected]
    assert positions == sorted(positions)
    assert "install_gate_intent_safety()" not in runtime

    assert "_work_target_updated" in navigation
    assert "_partition_ids_updated" in navigation
    assert "mqtt_navigation_target_stale_fields" in navigation
    assert "_navigation_intent_resolving" in navigation
    assert "current_physical_zone_stale" in navigation

    assert "Do not deepcopy the full session cache" in history
    assert "closed_zone_ids" in history
    assert "current_cycle_signature" in history
    assert "async_add_executor_job" in history

    assert "current_cycle_only" in map_api
    assert "include_current_cycle" in map_api
    assert "self.coordinator.set_command_target([zone_id], source=source)" in schedule
    assert "set_command_target" in services
    assert "set_pending_activity" in services
    assert "set_pending_activity" in mower
