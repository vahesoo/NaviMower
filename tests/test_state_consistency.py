"""Dependency-free checks for dock state, height filtering and gate arrival guard."""
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

functions = [
    node for node in tree.body
    if isinstance(node, ast.FunctionDef)
    and node.name
    in {
        "_as_int",
        "_as_float",
        "_normalize_cutting_height_mm",
        "_apply_gate_arrival_guard",
    }
]
module = ast.Module(body=functions, type_ignores=[])
ast.fix_missing_locations(module)
ns: dict[str, Any] = {
    "Any": Any,
    "CUTTING_HEIGHT_MIN_MM": 10,
    "CUTTING_HEIGHT_MAX_MM": 100,
    "GATE_ARRIVAL_GUARD_SECONDS": 120,
}
exec(compile(module, "coordinator.py", "exec"), ns)
height = ns["_normalize_cutting_height_mm"]
guard = ns["_apply_gate_arrival_guard"]
assert height(35) == 35
assert height(100) == 100
assert height(316) is None
assert height(0) is None

held = guard(
    target_ids=[13], target_source="private_work_target", physical_zone_id=24,
    guards={"gate": {"from_zone_id": 13, "to_zone_id": 24, "arrived_at": 100.0}},
    now_monotonic=130.0, command_fresh=False, is_returning=False,
)
assert held[0] == [24] and held[1] == "gate_arrival_guard"
assert held[2]["slug"] == "gate" and held[2]["age_seconds"] == 30.0
bypass = guard(
    target_ids=[13], target_source="private_work_target", physical_zone_id=24,
    guards={"gate": {"from_zone_id": 13, "to_zone_id": 24, "arrived_at": 100.0}},
    now_monotonic=130.0, command_fresh=True, is_returning=False,
)
assert bypass[:2] == ([13], "private_work_target")

# Extract only the dock resolver method into a tiny test class.
resolver = None
for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == "NavimowCoordinator":
        resolver = next(
            item for item in node.body
            if isinstance(item, ast.FunctionDef) and item.name == "_resolved_docked_state"
        )
        break
assert resolver is not None
mini = ast.ClassDef(
    name="Mini", bases=[], keywords=[], decorator_list=[],
    body=[resolver], type_params=[],
)
mod = ast.Module(body=[mini], type_ignores=[])
ast.fix_missing_locations(mod)
ns.update({
    "ACTIVITY_MOWING": "mowing", "ACTIVITY_PAUSED": "paused",
    "ACTIVITY_RETURNING": "returning", "MQTT_STATE_MOWING": 4,
    "MQTT_STATE_RETURNING": 5, "MQTT_STATE_MAPPING": 6,
    "MQTT_DOCKED_STATES": {2, 3}, "DOCKED_STATES": {"0101", "0102"},
})
exec(compile(mod, "coordinator.py", "exec"), ns)
resolve_docked = ns["Mini"]()._resolved_docked_state
assert resolve_docked("0101", 4, "mowing", None) == (False, "mqtt_active_state")
assert resolve_docked("0101", None, "mowing", None) == (False, "normalized_activity")
assert resolve_docked("", 3, "docked", None) == (True, "mqtt_docked_state")
assert resolve_docked("0101", None, "docked", None) == (True, "private_docked_state")

assert 'boundary.pop("height_set", None)' in source
assert '"cutting_height_supported": cutting_height_supported' in source
assert "merged.update(location)" in source
assert "start_new_mowing_cycle" in SERVICES.read_text(encoding="utf-8")
assert "start_new_mowing_cycle" in MOWER.read_text(encoding="utf-8")
print("state consistency tests passed")
