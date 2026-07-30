"""Dependency-free regression checks for target intent and gate safety."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = ROOT / "custom_components" / "navimower" / "coordinator.py"
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

services_source = SERVICES.read_text(encoding="utf-8")
mower_source = MOWER.read_text(encoding="utf-8")
assert "set_command_target" in services_source
assert "set_pending_activity" in services_source
assert "set_pending_activity" in mower_source
assert "if intent and latch is None:" in source
activity_body = mower_source.split("def activity", 1)[1].split("async def", 1)[0]
assert "LawnMowerActivity.DOCKED)" not in activity_body

print("navigation intent tests passed")
