"""Permanent regressions for cross-project safety hardening."""
from __future__ import annotations

import ast
from pathlib import Path

from diagnostics_contract import load_redactor

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_paused_returning_start_resumes_existing_task() -> None:
    source = (COMPONENT / "lawn_mower.py").read_text(encoding="utf-8")
    ast.parse(source)

    assert '_RESUMABLE_PAUSED_STATES = {STATE_PAUSED, "0221"}' in source
    assert "if state_code in _RESUMABLE_PAUSED_STATES:" in source
    assert 'source="lawn_mower.start_mowing_paused"' in source
    assert "async_resume_task(" in source
    assert "client.mow_zones" in source
    assert source.index("if state_code in _RESUMABLE_PAUSED_STATES:") < source.index(
        "client.mow_zones"
    )


def test_device_tracker_rejects_zero_fix_and_does_not_force_recorder_updates() -> None:
    source = (COMPONENT / "device_tracker.py").read_text(encoding="utf-8")
    ast.parse(source)

    assert "def _coordinates(" in source
    assert "if latitude == 0.0 and longitude == 0.0:" in source
    assert "def force_update(self) -> bool:" in source
    assert '"Do not create recorder rows when a polled coordinate did not change."' in source
    force_update_block = source.split("def force_update(self) -> bool:", 1)[1].split(
        "@property", 1
    )[0]
    assert "return False" in force_update_block


def test_service_zone_validation_uses_decoded_map_only() -> None:
    source = (COMPONENT / "services.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_decoded_map_zone_ids"
    )
    helper_source = ast.get_source_segment(source, helper) or ""
    assert 'map_data = (coordinator.data or {}).get("map")' in helper_source
    assert 'map_data.get("zones")' in helper_source
    assert '_validate_zone_ids(coordinator, requested_zones)' in source
    assert "Partial fallback" not in source  # implementation, not release-note prose


def test_disabled_schedule_is_not_trapped_by_semantic_validation() -> None:
    source = (COMPONENT / "services.py").read_text(encoding="utf-8")
    ast.parse(source)

    assert "if enabled:\n                if end_min <= start_min:" in source
    assert "if enabled:\n            periods.sort" in source
    assert "_validate_zone_ids(coordinator, zone_ids)" in source
    assert "Semantic validation is only for a schedule being enabled" in source


def test_firmware_version_looking_like_ipv4_survives_without_weakening_ip_redaction() -> None:
    redactor = load_redactor()
    assert redactor.REDACTION_VERSION >= 4

    clean = redactor.sanitize(
        {
            "firmwareVersion": "1.12.3.40",
            "network_note": "endpoint 192.0.2.4 unreachable",
            "ipAddress": "192.0.2.5",
        }
    )
    assert clean["firmwareVersion"] == "1.12.3.40"
    assert "192.0.2.4" not in clean["network_note"]
    assert clean["ipAddress"] == redactor.REDACTED

    mixed = redactor.sanitize(
        {"firmwareVersion": "build mirror 192.0.2.6"}
    )
    assert "192.0.2.6" not in mixed["firmwareVersion"]
