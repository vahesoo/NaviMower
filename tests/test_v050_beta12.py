"""Release regressions for Navimower 0.5.0-beta12."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
COORDINATOR = COMPONENT / "coordinator.py"
BINARY_SENSOR = COMPONENT / "binary_sensor.py"


def _load_single_function(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"Any": object}
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


def test_beta12_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta12"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta12.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Aggregate Gate required",
        "true + unknown",
        "entity ID",
        "individual",
        "0.4.0-beta7",
    ):
        assert marker in notes


def test_gate_aggregate_is_conservative_three_state_or() -> None:
    aggregate = _load_single_function(
        COORDINATOR,
        "_aggregate_gate_required_state",
    )

    assert aggregate(
        {
            "gate": {"required": True},
            "gate2": {"required": None},
        }
    ) == (True, ["gate"], ["gate2"])

    assert aggregate(
        {
            "gate": {"required": False},
            "gate2": {"required": False},
        }
    ) == (False, [], [])

    assert aggregate(
        {
            "gate": {"required": False},
            "gate2": {"required": None},
        }
    ) == (None, [], ["gate2"])

    assert aggregate({}) == (None, [], [])


def test_coordinator_publishes_aggregate_gate_diagnostics() -> None:
    source = COORDINATOR.read_text(encoding="utf-8")
    for marker in (
        '"gate_required": gate_required',
        '"gate_required_active_slugs": active_gate_slugs',
        '"gate_required_unknown_slugs": unknown_gate_slugs',
        '"gate_required_configured_count": len(gate_states)',
        "def aggregate_gate_state",
        "def aggregate_gate_attributes",
    ):
        assert marker in source


def test_binary_sensor_creates_one_mower_level_gate_required_entity() -> None:
    source = BINARY_SENSOR.read_text(encoding="utf-8")
    assert "class NavimowerAggregateGateRequiredBinarySensor" in source
    assert 'super().__init__(coordinator, "gate_required")' in source
    assert '_attr_translation_key = "gate_required"' in source
    assert "if coordinator.gates:" in source
    assert "entities.append(NavimowerAggregateGateRequiredBinarySensor(coordinator))" in source
    assert "aggregate_gate_state()" in source
    assert "aggregate_gate_attributes()" in source


def test_default_legacy_gate_entity_id_is_migrated_to_aggregate_unique_id() -> None:
    source = BINARY_SENSOR.read_text(encoding="utf-8")
    start = source.index("def _migrate_default_gate_required_to_aggregate")
    end = source.index("@dataclass", start)
    block = source[start:end]

    assert 'aggregate_unique_id = f"{coordinator.sn}_gate_required"' in block
    assert 'gate.slug == "gate"' in block
    assert 'f"{coordinator.sn}_gate_{default_gate.slug}_required"' in block
    assert "registry.async_get_entity_id(" in block
    assert "registry.async_update_entity(" in block
    assert "new_unique_id=aggregate_unique_id" in block


def test_individual_gate_path_sensors_remain_available() -> None:
    source = BINARY_SENSOR.read_text(encoding="utf-8")
    assert "NavimowerGateRequiredBinarySensor(coordinator, gate)" in source
    assert 'self._attr_name = f"{gate.name} path required"' in source
    assert "return self.coordinator.gate_state(self.gate)" in source


def test_gate_required_translation_exists() -> None:
    for path in (
        COMPONENT / "strings.json",
        COMPONENT / "translations" / "en.json",
    ):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["entity"]["binary_sensor"]["gate_required"]["name"] == "Gate required"
