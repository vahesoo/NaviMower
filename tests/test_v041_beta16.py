"""Regression contracts for Navimower 0.4.1-beta16."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta16_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    version = manifest["version"]
    assert version.startswith("0.4.1-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 16
    assert all(not requirement.startswith("zstandard") for requirement in manifest["requirements"])
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta16.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta16")
    for phrase in ("0103", "0301", "error_data", "Self-Checking", "6106", "6108"):
        assert phrase in notes


def test_beta16_runtime_state_and_error_contract() -> None:
    source = (COMPONENT / "beta16_runtime.py").read_text()
    ast.parse(source)
    assert '_STATE_IDLE = "0103"' in source
    assert '_STATE_FAULT = "0301"' in source
    assert '_STATE_LIFTED = "0302"' in source
    assert '_MQTT_STOPPED = 3' in source
    assert 'MQTT_DOCKED_STATES.discard(_MQTT_STOPPED)' in source
    assert 'VEHICLE_STATE_LABELS[_STATE_IDLE] = "Idle"' in source
    assert 'VEHICLE_STATE_LABELS[_STATE_FAULT] = "Error"' in source
    assert '"error_code"' in source
    assert '"error_title"' in source
    assert '"error_content"' in source
    assert '"error_kind"' in source
    assert '"fault"' in source
    assert '"safety"' in source


def test_beta16_mqtt_error_forces_private_detail_refresh() -> None:
    source = (COMPONENT / "beta16_runtime.py").read_text()
    assert 'state_name == "Error"' in source
    assert 'state_name == "Self-Checking"' in source
    assert '_mark_endpoints_due(self, "index2", "auth_list")' in source
    assert 'request_fast_refresh("MQTT state changed to Error")' in source
    assert 'request_fast_refresh("MQTT entered Self-Checking")' in source
    assert 'request_fast_refresh("MQTT state changed away from Error")' in source


def test_beta16_keeps_lifted_separate_from_numeric_fault_codes() -> None:
    source = (COMPONENT / "beta16_runtime.py").read_text()
    lifted_block = source.split("elif state_code == _STATE_LIFTED", 1)[1].split(
        "elif state_code == _STATE_FAULT", 1
    )[0]
    assert 'snapshot["error_code"] = None' in lifted_block
    assert 'snapshot["error_kind"] = "safety"' in lifted_block
    assert "180D" in source  # documentation explicitly says it is not mapped
    assert "6007" in source  # documentation explicitly says it is not mapped


def test_beta16_runtime_is_installed_after_transition_capture() -> None:
    services = (COMPONENT / "services.py").read_text()
    assert "from .beta16_runtime import install_beta16_runtime" in services
    assert services.index("install_state_transition_capture()") < services.index(
        "install_beta16_runtime()"
    )
