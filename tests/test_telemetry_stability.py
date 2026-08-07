"""Dependency-free checks for telemetry freshness and cycle stabilization."""
from __future__ import annotations

import ast
from pathlib import Path
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = ROOT / "custom_components" / "navimower" / "coordinator.py"
source = COORDINATOR.read_text(encoding="utf-8")
tree = ast.parse(source)

module_functions = [
    node
    for node in tree.body
    if isinstance(node, ast.FunctionDef)
    and node.name in {"_as_int", "_as_float", "_progress_percent"}
]
coordinator_class = next(
    node
    for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name == "NavimowCoordinator"
)
method_names = {
    "_age_since",
    "_private_endpoint_age",
    "_fresh_mqtt_battery",
    "_fresh_mqtt_progress_values",
    "_mark_display_cycle_reset",
    "_cycle_reset_guard_active",
    "_zero_coverage",
    "_stabilize_telemetry",
}
methods = [
    node
    for node in coordinator_class.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name in method_names
]
mini = ast.ClassDef(
    name="Mini",
    bases=[],
    keywords=[],
    decorator_list=[],
    body=methods,
    type_params=[],
)
module = ast.Module(body=[*module_functions, mini], type_ignores=[])
ast.fix_missing_locations(module)
namespace: dict[str, Any] = {
    "Any": Any,
    "time": time,
    "ACTIVITY_MOWING": "mowing",
    "ACTIVITY_PAUSED": "paused",
    "ACTIVITY_RETURNING": "returning",
    "ACTIVITY_DOCKED": "docked",
    "VENDOR_COMPLETION_PROGRESS_MIN": 95,
    "MQTT_TELEMETRY_STALE_SECONDS": 180,
    "CYCLE_RESET_STALE_GUARD_SECONDS": 3600,
}
exec(compile(module, "coordinator.py", "exec"), namespace)
Mini = namespace["Mini"]
Mini.private_poll_age = lambda self: None


def mower() -> Any:
    item = Mini()
    item.data = {}
    item._endpoint_status = {}
    item._mqtt_location = {}
    item._mqtt_battery = None
    item._mqtt_battery_last_update = None
    item._mqtt_progress_last_update = None
    item._mqtt_area_last_update = None
    item._progress_reset_pending = False
    item._coverage_reset_pending = False
    item._area_reset_pending = False
    item._cycle_reset_started_mono = None
    item._cycle_reset_reason = None
    item._cycle_reset_previous_area = None
    item._restored_telemetry = {}
    return item


# Active mower prefers fresh official MQTT battery over a coarse cloud value.
item = mower()
item._mqtt_battery = 98
item._mqtt_battery_last_update = time.monotonic()
previous = {"activity": "mowing", "battery": 100}
snapshot = {
    "activity": "mowing",
    "docked": False,
    "battery_private_cloud": 79,
    "mowing_progress_private_cloud": 10,
    "coverage": {"overall_pct": 10, "zones": []},
    "session_area_private_cloud": 100.0,
    "total_area_private_cloud": 1751.884,
}
item._stabilize_telemetry(snapshot, previous)
assert snapshot["battery"] == 98
assert snapshot["battery_source"] == "mqtt_state"

# Docked mower retains the already-smooth private-cloud charging value.
item = mower()
item._mqtt_battery = 60
item._mqtt_battery_last_update = time.monotonic()
previous = {"activity": "docked", "battery": 57}
snapshot = {
    "activity": "docked",
    "docked": True,
    "battery_private_cloud": 58,
    "mowing_progress_private_cloud": 0,
    "coverage": {"overall_pct": 0, "zones": []},
    "session_area_private_cloud": 0.0,
    "total_area_private_cloud": 1751.884,
}
item._stabilize_telemetry(snapshot, previous)
assert snapshot["battery"] == 58
assert snapshot["battery_source"] == "private_cloud"

# Starting a new cycle preserves the valid vendor counters exactly as received.
item = mower()
previous = {
    "activity": "docked",
    "battery": 100,
    "mowing_progress": 100,
    "coverage": {"overall_pct": 95, "finished_area": 1660.0, "zones": []},
    "session_area": 1773.48,
    "total_area": 1751.884,
}
snapshot = {
    "activity": "mowing",
    "docked": False,
    "battery_private_cloud": 99,
    "mowing_progress_private_cloud": 95,
    "coverage": {"overall_pct": 95, "finished_area": 1660.0, "zones": []},
    "session_area_private_cloud": 1773.48,
    "total_area_private_cloud": 1751.884,
}
item._stabilize_telemetry(snapshot, previous)
assert snapshot["mowing_progress"] == 95
assert snapshot["coverage"]["overall_pct"] == 95
assert snapshot["session_area"] == 1773.48
assert snapshot["cycle_value_reset_pending"] is False

# A low fresh row is accepted directly as the next vendor value.
previous = dict(snapshot)
item._mqtt_location = {
    "mowing_percentage": 5,
    "subtotal_area": 105.36,
}
item._mqtt_progress_last_update = time.monotonic()
item._mqtt_area_last_update = time.monotonic()
snapshot = {
    "activity": "mowing",
    "docked": False,
    "battery_private_cloud": 98,
    "mowing_progress_private_cloud": 100,
    "coverage": {"overall_pct": 5, "finished_area": 105.36, "zones": []},
    "session_area_private_cloud": 105.36,
    "total_area_private_cloud": 1751.884,
}
item._stabilize_telemetry(snapshot, previous)
assert snapshot["mowing_progress"] == 5
assert snapshot["coverage"]["overall_pct"] == 5
assert snapshot["session_area"] == 105.36

# A valid zero/regression inside the same cycle is preserved.
item = mower()
previous = {
    "activity": "mowing",
    "battery": 50,
    "mowing_progress": 51,
    "coverage": {"overall_pct": 49, "zones": []},
    "session_area": 903.39,
    "total_area": 1751.884,
}
snapshot = {
    "activity": "mowing",
    "docked": False,
    "battery_private_cloud": 49,
    "mowing_progress_private_cloud": 48,
    "coverage": {"overall_pct": 49, "zones": []},
    "session_area_private_cloud": 0.0,
    "total_area_private_cloud": None,
    "map": None,
}
item._stabilize_telemetry(snapshot, previous)
assert snapshot["mowing_progress"] == 48
assert snapshot["session_area"] == 0.0
assert snapshot["total_area"] == 1751.884
assert snapshot["total_area_source"] == "last_known"

print("telemetry stability tests passed")

# Resuming a paused nearly-complete job is not a new cycle.
item = mower()
previous = {
    "activity": "paused",
    "battery": 40,
    "mowing_progress": 96,
    "coverage": {"overall_pct": 95, "zones": []},
    "session_area": 1600.0,
}
snapshot = {
    "activity": "mowing",
    "docked": False,
    "battery_private_cloud": 39,
    "mowing_progress_private_cloud": 96,
    "coverage": {"overall_pct": 95, "zones": []},
    "session_area_private_cloud": 1600.0,
    "total_area_private_cloud": 1751.884,
}
item._stabilize_telemetry(snapshot, previous)
assert snapshot["mowing_progress"] == 96
assert snapshot["coverage"]["overall_pct"] == 95
assert snapshot["session_area"] == 1600.0
assert snapshot["cycle_value_reset_pending"] is False

print("resume guard tests passed")
