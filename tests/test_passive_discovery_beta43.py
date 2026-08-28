"""Regression guards for the beta43 passive MQTT discovery feature."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_passive_discovery_release_contract_survives_later_releases() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta43.md").read_text()
    assert "Passive MQTT discovery" in notes
    assert "gate_required" in notes


def test_beta43_general_flow_exposes_and_persists_passive_discovery() -> None:
    source = (COMPONENT / "config_flow.py").read_text()
    ast.parse(source)
    assert '_PASSIVE_DISCOVERY_OPTION = "passive_discovery"' in source
    assert "async def async_step_general" in source
    assert "payload.pop(_PASSIVE_DISCOVERY_OPTION, None)" in source
    assert "data[_PASSIVE_DISCOVERY_OPTION] = enabled" in source
    assert "self._options().get(_PASSIVE_DISCOVERY_OPTION, False)" in source
    assert 'options.pop("passive_discovery", None)' not in source


def test_beta43_startup_keeps_only_reenabled_passive_discovery() -> None:
    source = (COMPONENT / "__init__.py").read_text()
    ast.parse(source)
    assert '_DEPRECATED_DIAGNOSTICS_OPTIONS = {"diagnostics_detail", "passive_discovery"}' in source
    assert '_BETA43_REENABLED_DIAGNOSTICS_OPTIONS = {"passive_discovery"}' in source
    assert "key not in _BETA43_REENABLED_DIAGNOSTICS_OPTIONS" in source


def test_beta43_mqtt_bridge_already_collects_bounded_samples() -> None:
    source = (COMPONENT / "mqtt.py").read_text()
    ast.parse(source)
    for marker in (
        "self._discovery_enabled",
        "mqtt_discovery_topics(device_id)",
        "include_samples=True",
        "sanitize_discovery_payload(payload)",
        "def diagnostic_discovery",
    ):
        assert marker in source
