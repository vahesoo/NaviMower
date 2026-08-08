"""Regression contracts for Navimower 0.4.1-beta5 passive discovery."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _load_discovery():
    path = COMPONENT / "discovery.py"
    spec = importlib.util.spec_from_file_location("navimower_discovery_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_beta5_discovery_notes():
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta5.md").read_text()
    assert "Passive protocol discovery" in notes
    assert "current-device-only" in notes
    assert "off by default" in notes


def test_discovery_topic_is_current_mower_only():
    discovery = _load_discovery()
    assert discovery.mqtt_discovery_topic("ABC123") == "/downlink/vehicle/ABC123/#"


def test_discovery_samples_redact_secrets_and_url_queries():
    discovery = _load_discovery()
    payload = json.dumps({
        "eventCode": 2102,
        "token": "secret-token",
        "device_id": "secret-device",
        "url": "https://camera.example/live/path?signature=secret",
        "message": "camera ready",
    }).encode()
    sample = discovery.sanitize_discovery_payload(payload)
    assert sample["eventCode"] == 2102
    assert sample["token"] == "<redacted>"
    assert sample["device_id"] == "<redacted>"
    assert sample["url"] == "https://camera.example/live/path"
    assert sample["message"] == "camera ready"


def test_structure_summary_keeps_schema_not_ordinary_values():
    discovery = _load_discovery()
    summary = discovery.structure_summary({"type": 7, "nested": {"cameraUrl": "secret"}})
    assert "nested.cameraUrl" in summary["key_paths"]
    assert "type=7" in summary["observed_type_values"]
    assert "secret" not in repr(summary)


def test_beta5_option_service_and_diagnostics_are_wired():
    const = (COMPONENT / "const.py").read_text()
    flow = (COMPONENT / "config_flow.py").read_text()
    mqtt = (COMPONENT / "mqtt.py").read_text()
    services = (COMPONENT / "services.py").read_text()
    diagnostics = (COMPONENT / "diagnostics_export.py").read_text()
    client = (COMPONENT / "api" / "client.py").read_text()
    assert 'OPT_PASSIVE_DISCOVERY: Final = "passive_discovery"' in const
    assert "DEFAULT_PASSIVE_DISCOVERY: Final = False" in const
    assert "OPT_PASSIVE_DISCOVERY" in flow
    assert "mqtt_discovery_topic(device_id)" in mqtt
    assert "diagnostic_discovery" in mqtt
    assert "mark_discovery_event" in services
    assert '"mqtt_discovery"' in diagnostics
    assert '"cloud_request_inventory"' in diagnostics
    assert "discovery_inventory" in client
