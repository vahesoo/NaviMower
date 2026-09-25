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
        {
            "_as_int",
            "_dedupe_zone_ids",
            "_resolve_navigation_target_ids",
            "_published_navigation_target",
        },
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

    # A periodically refreshed packed target from a completed older segment
    # must not outrank a one-zone task that agrees with the live physical zone.
    result = resolve(
        **{
            **common,
            "dock_zone_id": None,
            "physical_zone_id": 36,
            "mqtt_work_target": 37,
            "mqtt_partition_ids": [36],
            "cloud_zone_ids": [36],
        }
    )
    assert result[:2] == ([36], "mqtt_partition_ids")

    # Returning navigation keeps the internal route target so gate intent can
    # still follow the route back through mapped zones.
    returning = resolve(
        **{
            **common,
            "is_returning": True,
            "dock_zone_id": None,
            "physical_zone_id": 36,
            "mqtt_work_target": 37,
            "mqtt_partition_ids": [36],
            "cloud_zone_ids": [36],
        }
    )
    assert returning[:2] == ([37], "mqtt_work_target")

    publish = namespace["_published_navigation_target"]
    assert publish(
        target_ids=returning[0],
        target_source=returning[1],
        is_returning=True,
        dock_zone_id=None,
    ) == ([], "returning_to_dock")
    assert publish(
        target_ids=[13],
        target_source="returning_to_dock",
        is_returning=True,
        dock_zone_id=13,
    ) == ([13], "returning_to_dock")
    assert publish(
        target_ids=[36],
        target_source="mqtt_partition_ids",
        is_returning=False,
        dock_zone_id=None,
    ) == ([36], "mqtt_partition_ids")


def test_public_target_is_task_only_and_never_revives_retained_route_intent() -> None:
    namespace = load_functions(
        NAVIGATION,
        {
            "_as_int",
            "_zone_ids",
            "_resolve_public_task_target",
            "_task_target_active",
        },
        {"Any": Any},
    )
    resolve = namespace["_resolve_public_task_target"]
    active = namespace["_task_target_active"]

    assert not active({"activity": "docked", "docked": True, "state_code": "0102"})
    assert not active({"activity": "returning", "docked": False, "state_code": "0220"})
    assert not active({"activity": "paused", "docked": False, "state_code": "0202"})
    assert active({"activity": "mowing", "docked": False, "state_code": "0210"})
    assert active({"activity": "paused", "docked": False, "state_code": "0211"})

    common = dict(
        is_docked=False,
        is_returning=False,
        task_active=True,
        command_target_ids=[],
        command_target_fresh=False,
        mqtt_partition_ids=[],
        mqtt_partition_fresh=False,
        cloud_zone_ids=[],
        mqtt_work_target=None,
        mqtt_work_target_fresh=False,
        cloud_work_target=None,
    )

    # Idle/no-task state never inherits a retained target from the navigation
    # resolver. There is intentionally no last_known input in this API.
    assert resolve(**{**common, "task_active": False, "cloud_work_target": 37}) == (
        [],
        "none",
    )

    # Return routing stays available internally to gate logic but public Target
    # zone is empty even if a route/work target still exists.
    assert resolve(
        **{
            **common,
            "is_returning": True,
            "mqtt_partition_ids": [37],
            "mqtt_partition_fresh": True,
            "mqtt_work_target": 37,
            "mqtt_work_target_fresh": True,
        }
    ) == ([], "returning_to_dock")

    # Fresh selected task zones are the strongest vendor-owned public target.
    assert resolve(
        **{
            **common,
            "mqtt_partition_ids": [92, 91, 5],
            "mqtt_partition_fresh": True,
            "cloud_zone_ids": [41],
            "mqtt_work_target": 5,
            "mqtt_work_target_fresh": True,
        }
    ) == ([92, 91, 5], "mqtt_partition_ids")

    # A fresh empty partition list is the observed Mow All form. It suppresses
    # stale cloud selection and falls through to the fresh immediate work target.
    assert resolve(
        **{
            **common,
            "mqtt_partition_ids": [],
            "mqtt_partition_fresh": True,
            "cloud_zone_ids": [92, 91, 5],
            "mqtt_work_target": 5,
            "mqtt_work_target_fresh": True,
        }
    ) == ([5], "mqtt_work_target")

    # When MQTT task selection is stale/unavailable, the private task selection
    # is the bounded fallback. A local command still wins while fresh.
    assert resolve(
        **{
            **common,
            "cloud_zone_ids": [91, 5],
            "mqtt_work_target": 5,
            "mqtt_work_target_fresh": True,
        }
    ) == ([91, 5], "private_current_zones")
    assert resolve(
        **{
            **common,
            "command_target_ids": [42],
            "command_target_fresh": True,
            "mqtt_partition_ids": [92, 91, 5],
            "mqtt_partition_fresh": True,
        }
    ) == ([42], "ha_command")


def test_immediate_target_is_single_and_does_not_guess_multi_zone_order() -> None:
    namespace = load_functions(
        NAVIGATION,
        {"_as_int", "_zone_ids", "_resolve_immediate_target"},
        {"Any": Any},
    )
    resolve = namespace["_resolve_immediate_target"]

    common = dict(
        is_docked=False,
        is_returning=False,
        task_active=True,
        command_target_ids=[],
        command_target_fresh=False,
        planned_zone_ids=[10, 20, 30],
        mqtt_work_target=None,
        mqtt_work_target_fresh=False,
        mqtt_work_target_after_command=False,
        physical_zone_id=None,
        physical_zone_fresh=False,
        cloud_work_target=None,
    )

    # Multi-zone selection alone is not enough to claim which zone is next.
    assert resolve(**common) == ([], "none")

    # A fresh local command owns dispatch until a vendor work target observed
    # after that command confirms the immediate target.
    assert resolve(
        **{
            **common,
            "command_target_ids": [10, 20, 30],
            "command_target_fresh": True,
            "mqtt_work_target": 20,
            "mqtt_work_target_fresh": True,
            "mqtt_work_target_after_command": False,
        }
    ) == ([10], "ha_command")
    assert resolve(
        **{
            **common,
            "command_target_ids": [10, 20, 30],
            "command_target_fresh": True,
            "mqtt_work_target": 20,
            "mqtt_work_target_fresh": True,
            "mqtt_work_target_after_command": True,
        }
    ) == ([20], "mqtt_work_target")

    # While actually mowing a selected zone, fresh physical-zone evidence
    # keeps Target zone useful even if the work-target field is momentarily absent.
    assert resolve(
        **{
            **common,
            "physical_zone_id": 20,
            "physical_zone_fresh": True,
        }
    ) == ([20], "current_physical_zone")

    # One-zone tasks are safe to expose even without a separate work target.
    assert resolve(
        **{
            **common,
            "planned_zone_ids": [30],
        }
    ) == ([30], "planned_single_zone")

    # Returning/docked states never keep a mowing target alive.
    assert resolve(**{**common, "is_returning": True}) == (
        [],
        "returning_to_dock",
    )
    assert resolve(**{**common, "is_docked": True}) == ([], "docked")

def test_docked_target_is_stable_across_pose_heartbeats() -> None:
    namespace = load_functions(
        COORDINATOR,
        {
            "_as_int",
            "_dedupe_zone_ids",
            "_resolve_navigation_target_ids",
        },
        {"Any": Any},
    )
    resolve = namespace["_resolve_navigation_target_ids"]

    retained = dict(
        is_returning=False,
        dock_zone_id=None,
        physical_zone_id=None,
        command_target_ids=[],
        command_target_fresh=False,
        mqtt_work_target=None,
        cloud_work_target=37,
        mqtt_partition_ids=[],
        cloud_zone_ids=[37],
        last_target_ids=[37],
    )

    # A confirmed dock is authoritative regardless of whether the periodic
    # dock XY heartbeat is currently fresh or has already aged out. Pose
    # freshness is deliberately not an input to navigation_docked().
    for _pose_valid in (True, False):
        result = resolve(
            **{
                **retained,
                "is_docked": True,
            }
        )
        assert result == ([], "docked", False)

    # The upstream dock resolver releases a stale private-cloud dock as soon as
    # a fresh HA mowing transition is pending. The target resolver therefore
    # receives is_docked=False and exposes the newly commanded target.
    commanded = resolve(
        **{
            **retained,
            "is_docked": False,
            "command_target_ids": [37],
            "command_target_fresh": True,
        }
    )
    assert commanded == ([37], "ha_command", False)


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
    assert "_resolve_public_task_target" in navigation
    assert "_resolve_immediate_target" in navigation
    assert "navigation_target_zone_ids" in navigation
    assert "planned_zone_ids" in navigation
    assert "planned_zones" in navigation
    assert "target_zone_task_active" in navigation

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
