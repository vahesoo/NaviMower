"""Historical regression contracts for Navimower 0.4.1-beta15."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta15_release_notes_remain_historical() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"].startswith("0.4.1-") or manifest["version"] == "0.4.1"
    assert all(not requirement.startswith("zstandard") for requirement in manifest["requirements"])
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta15.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta15")
    assert "state transition" in notes.lower()
    assert "index2" in notes
    assert "boolState" in notes
    assert "immediately" in notes and "after 1 s" in notes and "after 5 s" in notes


def test_beta15_capture_reads_state_endpoints_and_is_bounded() -> None:
    source = (COMPONENT / "state_transition_capture.py").read_text()
    assert "_CAPTURE_LIMIT = 30" in source
    assert "_PHASE_DELAYS = (0.0, 1.0, 5.0)" in source
    assert "client.index2" in source
    assert "client.auth_list" in source
    assert "client.location" in source
    assert '"boolState_delta"' in source
    assert '"code_candidates"' in source
    assert '"mqtt_named_state"' in source
    assert '"mqtt_numeric_state_action"' in source


def test_beta15_hook_and_native_diagnostics_contract() -> None:
    services = (COMPONENT / "services.py").read_text()
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    assert "install_state_transition_capture" in services
    assert "install_state_transition_capture()" in services
    assert 'document["state_transition_capture"]' in diagnostics
    assert "state_transition_diagnostics" in diagnostics
