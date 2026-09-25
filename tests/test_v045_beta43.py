"""Regression contracts for Navimower 0.4.5-beta43 target semantics."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
NAVIGATION = COMPONENT / "navigation_intent.py"


def _load_navigation_functions(names: set[str]) -> dict[str, Any]:
    source = NAVIGATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, Any] = {"Any": Any}
    exec(compile(module, str(NAVIGATION), "exec"), namespace)
    return namespace


def test_beta43_release_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.5-beta43"

    notes = (ROOT / ".github" / "release-notes" / "0.4.5-beta43.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "Target zone",
        "Planned zones",
        "immediate target",
        "automation",
        "target_zone_ids",
        "Gate",
    ):
        assert phrase in notes


def test_beta43_target_sensor_and_planned_sensor_contract() -> None:
    sensor = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    navigation = NAVIGATION.read_text(encoding="utf-8")
    mower = (COMPONENT / "lawn_mower.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")

    assert 'key="target_zone"' in sensor
    assert '"zone_id": d.get("target_zone_id")' in sensor
    assert '"planned_zone_ids": d.get("planned_zone_ids")' in sensor
    assert '"source": d.get("target_zone_immediate_source")' in sensor

    assert 'key="planned_zones"' in sensor
    assert 'translation_key="planned_zones"' in sensor
    assert '"zone_ids": d.get("planned_zone_ids")' in sensor

    assert 'current["planned_zone_ids"] = planned_ids' in navigation
    assert '"No planned zones"' in navigation
    assert 'current["target_zone_ids"] = planned_ids' in navigation
    assert 'current["target_zone_source"] = planned_source' in navigation
    assert 'current["target_zone_id"] = (' in navigation
    assert 'current["target_zone"] = _target_state(snapshot, immediate_ids)' in navigation
    assert 'current["target_zone_immediate_source"] = immediate_source' in navigation

    assert '"planned_zones": data.get("planned_zones")' in mower
    assert '"planned_zone_ids": data.get("planned_zone_ids")' in mower
    assert '"target_zone_id": data.get("target_zone_id")' in diagnostics
    assert '"target_zone_immediate_source": data.get("target_zone_immediate_source")' in diagnostics
    assert '"planned_zone_ids": deepcopy(data.get("planned_zone_ids") or [])' in diagnostics


def test_beta43_immediate_target_does_not_guess_multi_zone_order() -> None:
    namespace = _load_navigation_functions(
        {"_as_int", "_zone_ids", "_resolve_immediate_target"}
    )
    resolve = namespace["_resolve_immediate_target"]

    common = {
        "is_docked": False,
        "is_returning": False,
        "task_active": True,
        "command_target_ids": [],
        "command_target_fresh": False,
        "planned_zone_ids": [1, 2, 3],
        "mqtt_work_target": None,
        "mqtt_work_target_fresh": False,
        "mqtt_work_target_after_command": False,
        "physical_zone_id": None,
        "physical_zone_fresh": False,
        "cloud_work_target": None,
    }

    assert resolve(**common) == ([], "none")

    # Before a new vendor work-target sample arrives, the first requested zone
    # is the bounded dispatch target.
    assert resolve(
        **{
            **common,
            "command_target_ids": [1, 2, 3],
            "command_target_fresh": True,
            "mqtt_work_target": 2,
            "mqtt_work_target_fresh": True,
            "mqtt_work_target_after_command": False,
        }
    ) == ([1], "ha_command")

    # Once vendor work-target evidence is newer than the command, it owns the
    # public Target zone even while the original command TTL remains active.
    assert resolve(
        **{
            **common,
            "command_target_ids": [1, 2, 3],
            "command_target_fresh": True,
            "mqtt_work_target": 2,
            "mqtt_work_target_fresh": True,
            "mqtt_work_target_after_command": True,
        }
    ) == ([2], "mqtt_work_target")

    # Physical-zone evidence retains the active zone while mowing if the vendor
    # work-target field is temporarily absent.
    assert resolve(
        **{
            **common,
            "physical_zone_id": 2,
            "physical_zone_fresh": True,
        }
    ) == ([2], "current_physical_zone")


def test_beta43_planned_zones_keeps_multi_zone_task_selection() -> None:
    namespace = _load_navigation_functions(
        {"_as_int", "_zone_ids", "_resolve_public_task_target"}
    )
    resolve = namespace["_resolve_public_task_target"]

    result = resolve(
        is_docked=False,
        is_returning=False,
        task_active=True,
        command_target_ids=[],
        command_target_fresh=False,
        mqtt_partition_ids=[9, 7, 5],
        mqtt_partition_fresh=True,
        cloud_zone_ids=[1],
        mqtt_work_target=5,
        mqtt_work_target_fresh=True,
        cloud_work_target=1,
    )
    assert result == ([9, 7, 5], "mqtt_partition_ids")


def test_beta43_translations_include_planned_zones() -> None:
    for path in (
        COMPONENT / "strings.json",
        COMPONENT / "translations" / "en.json",
    ):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["entity"]["sensor"]["planned_zones"]["name"] == "Planned zones"
