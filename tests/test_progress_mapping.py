"""Dependency-free regression checks for vendor progress normalization."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = ROOT / "custom_components" / "navimower" / "coordinator.py"

source = COORDINATOR.read_text(encoding="utf-8")
tree = ast.parse(source)
selected = [
    node
    for node in tree.body
    if isinstance(node, ast.FunctionDef)
    and node.name in {"_as_float", "_progress_percent"}
]
module = ast.Module(body=selected, type_ignores=[])
ast.fix_missing_locations(module)
namespace: dict[str, Any] = {"Any": Any}
exec(compile(module, "coordinator.py", "exec"), namespace)
progress = namespace["_progress_percent"]

assert progress(None) is None
assert progress(-1) is None
assert progress(0) == 0
assert progress(0.84) == 84
assert progress(4) == 4
assert progress(84) == 84
assert progress(8400) == 84
assert progress(10_100) is None

assert 'detail["vendor_percentage"] = detail.get("percentage")' in source
assert '("map_work_position", work_progress)' in source
assert '("mowing_progress", private_mowing_progress)' not in source
assert '("mqtt_task_percentage", mqtt_progress["mowing_percentage"])' in source
assert '("private_task_percentage", private_progress)' in source
assert '("mqtt_map_work_position", mqtt_progress["work_progress"])' in source
assert '("mqtt_route_progress", mqtt_progress["route_progress"])' in source
assert 'snapshot["active_zone_progress"]' in source
completion = source.split("def _session_completed", 1)[1].split("def ", 1)[0]
# Completion may be resolved directly from live progress or, in later builds,
# from history's current-cycle confirmation set. Keep this regression semantic.
assert (
    'item.get("progress")' in completion
    or 'task_zone_completion_confirmed' in completion
)

print("progress mapping tests passed")
