"""Regression contracts for Navimower 0.4.1-beta7 runtime hotfix."""
from __future__ import annotations

import ast
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta7_notes() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta7.md").read_text()
    assert "structure_summary" in notes
    assert "services.yaml" in notes


def test_mqtt_bridge_imports_all_discovery_helpers_it_calls() -> None:
    source = (COMPONENT / "mqtt.py").read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "discovery":
            imported.update(alias.name for alias in node.names)
    required = {"mqtt_discovery_topic", "sanitize_discovery_payload", "structure_summary"}
    assert required <= imported
    loaded = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    assert required <= loaded


def test_services_yaml_is_valid_and_marker_selector_targets_navimower() -> None:
    services = yaml.safe_load((COMPONENT / "services.yaml").read_text())
    selector = services["mark_discovery_event"]["fields"]["device_id"]["selector"]["device"]
    assert selector["integration"] == "navimower"
