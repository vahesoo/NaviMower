"""Regression contracts for Navimower 0.4.1-beta9."""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _load_discovery():
    path = COMPONENT / "discovery.py"
    spec = importlib.util.spec_from_file_location("navimower_beta9_discovery", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_beta9_version_and_notes() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta9.md").read_text()
    assert "Problem" in notes
    assert "index2" in notes
    assert "/downlink/#" in notes
    assert "off by default" in notes


def test_problem_latch_is_persisted_and_cloud_cleared() -> None:
    source = (COMPONENT / "coordinator.py").read_text()
    assert "self._problem_latched = False" in source
    assert 'problem = cached.get("problem")' in source
    assert '"problem": {' in source
    assert "_private_problem_clear_confirmed" in source
    assert 'source="private_cloud_clear"' in source
    assert "confirmed_clear=True" in source
    assert "problem_diagnostics" in source
    assert 'snapshot["error"] = True' in source
    ast.parse(source)


def test_mqtt_named_state_does_not_depend_on_battery() -> None:
    source = (COMPONENT / "coordinator.py").read_text()
    assert "valid_battery = battery is not None and 0 <= battery <= 100" in source
    assert "if not valid_battery and not state_name:" in source
    assert 'state_name == "isLifted"' in source
    assert 'source="mqtt_state"' in source
    assert "_fresh_mqtt_named_state" in source


def test_wider_discovery_keeps_legacy_helper_and_filters_vehicle_samples() -> None:
    discovery = _load_discovery()
    assert discovery.mqtt_discovery_topic("ABC123") == "/downlink/vehicle/ABC123/#"
    assert discovery.mqtt_discovery_topics("ABC123") == ("/downlink/#",)
    mqtt = (COMPONENT / "mqtt.py").read_text()
    assert "for discovery_topic in mqtt_discovery_topics(device_id):" in mqtt
    assert "account_event = bool(" in mqtt
    assert '"/vehicle/" not in topic_text' in mqtt
    assert '"scope": "current_device_and_account_events"' in mqtt
    ast.parse(mqtt)


def test_problem_history_is_exposed_in_diagnostics() -> None:
    source = (COMPONENT / "diagnostics_export.py").read_text()
    assert "coordinator.problem_diagnostics()" in source
    assert '"problem_history": sanitize(deepcopy(problem_history))' in source
    assert '"problem_source": data.get("problem_source")' in source
    ast.parse(source)
