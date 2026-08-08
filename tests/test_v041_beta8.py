"""Regression contracts for Navimower 0.4.1-beta8."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta8_version_and_notes() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta8.md").read_text()
    assert "0302" in notes
    assert "get-hint-error-compress" in notes


def test_lifted_state_mapping_is_explicit() -> None:
    source = (COMPONENT / "const.py").read_text()
    assert 'STATE_LIFTED: Final = "0302"' in source
    assert 'STATE_LIFTED: ACTIVITY_ERROR' in source
    assert 'STATE_LIFTED: "Lifted"' in source


def test_hint_error_endpoint_is_polled_and_cached() -> None:
    const = (COMPONENT / "const.py").read_text()
    coordinator = (COMPONENT / "coordinator.py").read_text()
    assert '"errors": 30' in const
    assert '"errors": 60' in const
    assert '"errors": lambda: self.client.errors(sn, vtype)' in coordinator
    assert '"hint_error_compress": hint_errors' in coordinator


def test_mqtt_lifted_state_is_published_immediately() -> None:
    source = (COMPONENT / "coordinator.py").read_text()
    assert 'state_name == "isLifted"' in source
    assert 'snapshot["state"] = "Lifted"' in source
    assert 'snapshot["activity"] = ACTIVITY_ERROR' in source
    assert 'request_fast_refresh("MQTT state changed to isLifted")' in source
