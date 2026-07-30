"""Dependency-free regression test for cached map geometry migration."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "custom_components" / "navimower" / "coordinator.py").read_text()
tree = ast.parse(source)
selected = [
    node
    for node in tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name in {"deepcopy_json", "_normalize_cached_geometry"}
]
module = ast.Module(body=selected, type_ignores=[])
ast.fix_missing_locations(module)
namespace: dict[str, Any] = {"json": json, "Any": Any}
exec(compile(module, "coordinator.py", "exec"), namespace)
normalize = namespace["_normalize_cached_geometry"]

old = {
    "zones": [{"id": 13}],
    "obstacles": [[[1, 2], [3, 4]]],
    "vision_off": [[[5, 6], [7, 8]]],
    "tunnels": [{"id": 27, "points": [[0, 0], [1, 1]]}],
}
normalized = normalize(old)
assert normalized["off_limit_areas"] == old["obstacles"]
assert normalized["vf_off_areas"] == old["vision_off"]
assert normalized["channels"] == old["tunnels"]
assert "obstacles" not in normalized
assert "vision_off" not in normalized
assert "tunnels" not in normalized
assert normalized["zones"] == old["zones"]
assert old.get("off_limit_areas") is None  # input was not mutated

current = {
    "off_limit_areas": [1],
    "vf_off_areas": [2],
    "channels": [3],
}
assert normalize(current) == current

print("map cache migration tests passed")
