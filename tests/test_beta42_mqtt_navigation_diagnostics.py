"""Regression checks for gate-transition diagnostics independent of formatting.

The completed report is now sanitized once so repeated identifiers can be
removed across sections. Do not require a per-section sanitizer expression.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTICS = ROOT / "custom_components" / "navimower" / "diagnostics.py"


def test_mqtt_navigation_snapshot_preserves_gate_evidence_without_aliasing():
    tree = ast.parse(DIAGNOSTICS.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_mqtt_navigation_diagnostics"
    )
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"Any": Any, "deepcopy": deepcopy}
    exec(compile(module, str(DIAGNOSTICS), "exec"), namespace)
    location = {
        "work_sub_action": 2,
        "work_target_zone": 37,
        "mow_boundary": True,
        "partition_ids": [36, 37],
        "unrelated_large_payload": "not a navigation field",
    }
    data = {
        "mqtt_pose_age": 3.0,
        "current_physical_zone_id": 36,
        "target_zone_ids": [37],
        "target_zone_source": "mqtt_work_target",
        "gate_states": {"gate": {"required": True}},
        "gate_arrival_guards": {"gate": {"to_zone_id": 37}},
    }
    snapshot = namespace["_mqtt_navigation_diagnostics"](
        SimpleNamespace(_mqtt_location=location), data
    )
    assert snapshot["cached_location"] == {
        key: value for key, value in location.items()
        if key != "unrelated_large_payload"
    }
    assert snapshot["physical_zone_id"] == 36
    assert snapshot["target_zone_ids"] == [37]
    assert snapshot["pose_age_s"] == 3.0
    assert snapshot["gate_states"] == data["gate_states"]
    assert snapshot["gate_arrival_guards"] == data["gate_arrival_guards"]
    snapshot["cached_location"]["partition_ids"].append(99)
    snapshot["gate_states"]["gate"]["required"] = False
    assert location["partition_ids"] == [36, 37]
    assert data["gate_states"]["gate"]["required"] is True
