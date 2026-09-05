"""Dependency-free regression checks for target intent and gate safety."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = ROOT / "custom_components" / "navimower" / "coordinator.py"
GATE_SAFETY = ROOT / "custom_components" / "navimower" / "gate_intent_safety.py"
RUNTIME = ROOT / "custom_components" / "navimower" / "runtime.py"
SCHEDULE = ROOT / "custom_components" / "navimower" / "navimower_schedule.py"
SERVICES = ROOT / "custom_components" / "navimower" / "services.py"
MOWER = ROOT / "custom_components" / "navimower" / "lawn_mower.py"

source = COORDINATOR.read_text(encoding="utf-8")
tree = ast.parse(source)
selected = [
    node
    for node in tree.body
    if isinstance(node, ast.FunctionDef)
    and node.name in {"_as_int", "_dedupe_zone_ids", "_resolve_navigation_target_ids"}
]
module = ast.Module(body=selected, type_ignores=[])
ast.fix_missing_locations(module)
namespace: dict[str, Any] = {"Any": Any}
exec(compile(module, "coordinator.py", "exec"), namespace)
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
    **{**common, "command_target_ids": [13], "command_target_fresh": True,
       "cloud_work_target": 24, "cloud_zone_ids": [13]}
)
assert result[:2] == ([13], "ha_command"), result

result = resolve(
    **{**common, "cloud_work_target": 24, "cloud_zone_ids": [13]}
)
assert result[:2] == ([13], "private_current_zones"), result

result = resolve(**{**common, "is_returning": True, "command_target_ids": [24],
                    "command_target_fresh": True})
assert result[:2] == ([13], "returning_to_dock"), result

result = resolve(**{**common, "is_docked": True, "command_target_ids": [13],
                    "command_target_fresh": True})
assert result == ([], "docked", False), result

result = resolve(**{**common, "last_target_ids": [24]})
assert result == ([24], "last_known", False), result

result = resolve(
    **{**common, "physical_zone_id": 13, "command_target_ids": [13],
       "command_target_fresh": True, "mqtt_work_target": 13}
)
assert result == ([13], "ha_command_confirmed", True), result

# Reproduce the field failure from 2026-09-05: the mower is physically in zone
# 36 and Schedule explicitly dispatches zone 36, while an older 36 -> 37 gate
# latch remains cached. The safety layer must classify and clear that latch.
gate_source = GATE_SAFETY.read_text(encoding="utf-8")
gate_tree = ast.parse(gate_source)
gate_selected = [
    node
    for node in gate_tree.body
    if isinstance(node, ast.FunctionDef)
    and node.name in {
        "_as_int",
        "_single_zone",
        "_latch_conflicts_with_zone",
        "_clear_conflicting_latches",
    }
]
gate_module = ast.Module(body=gate_selected, type_ignores=[])
ast.fix_missing_locations(gate_module)
gate_namespace: dict[str, Any] = {"Any": Any}
exec(compile(gate_module, "gate_intent_safety.py", "exec"), gate_namespace)

single_zone = gate_namespace["_single_zone"]
conflicts = gate_namespace["_latch_conflicts_with_zone"]
clear_latches = gate_namespace["_clear_conflicting_latches"]

assert single_zone([36]) == 36
assert single_zone([36, 36]) == 36
assert single_zone([36, 37]) is None
assert conflicts({"from_zone_id": 36, "to_zone_id": 37}, 36) is True
assert conflicts({"from_zone_id": 36, "to_zone_id": 37}, 37) is False
assert conflicts({"from_zone_id": 36, "to_zone_id": 36}, 36) is False

class FakeCoordinator:
    def __init__(self) -> None:
        self._gate_latches = {
            "gate": {"from_zone_id": 36, "to_zone_id": 37},
            "other": {"from_zone_id": 37, "to_zone_id": 36},
        }
        self.cancelled: list[str] = []

    def _cancel_gate_release(self, slug: str) -> None:
        self.cancelled.append(slug)

fake = FakeCoordinator()
removed = clear_latches(fake, 36)
assert removed == ["gate"], removed
assert "gate" not in fake._gate_latches
assert "other" in fake._gate_latches
assert fake.cancelled == ["gate"]

runtime_source = RUNTIME.read_text(encoding="utf-8")
schedule_source = SCHEDULE.read_text(encoding="utf-8")
services_source = SERVICES.read_text(encoding="utf-8")
mower_source = MOWER.read_text(encoding="utf-8")
assert "install_gate_intent_safety" in runtime_source
assert "_SAME_ZONE_COMMAND_GATE_GUARD_SECONDS = 120.0" in gate_source
assert "stale_vendor_target_after_same_zone_command" in gate_source
assert "self.coordinator.set_command_target([zone_id], source=source)" in schedule_source
assert "set_command_target" in services_source
assert "set_pending_activity" in services_source
assert "set_pending_activity" in mower_source
assert "if intent and latch is None:" in source
activity_body = mower_source.split("def activity", 1)[1].split("async def", 1)[0]
assert "LawnMowerActivity.DOCKED)" not in activity_body

print("navigation intent tests passed")
