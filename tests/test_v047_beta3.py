"""Release contract for Navimower 0.4.7-beta3."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta3_version_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.7-beta3"
    notes = (ROOT / ".github" / "release-notes" / "0.4.7-beta3.md").read_text(
        encoding="utf-8"
    )
    assert notes.startswith("title: Navimower 0.4.7-beta3\n")
    for marker in (
        "Numeric MQTT state semantics",
        "Per-type MQTT source-time arbitration",
        "zone 36 -> 37",
        "MQTT keepalive remains unchanged",
    ):
        assert marker in notes


def test_beta3_numeric_state_three_is_not_charging_or_docked() -> None:
    const = (COMPONENT / "const.py").read_text(encoding="utf-8")
    state = (COMPONENT / "state_semantics.py").read_text(encoding="utf-8")
    schedule = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")

    assert "MQTT_STATE_PAUSED_OR_IDLE: Final = 3" in const
    assert "MQTT_STATE_CHARGING" not in const
    assert "MQTT_DOCKED_STATES: Final = {MQTT_STATE_DOCKED}" in const
    assert "pause/idle hint" in state
    assert "MQTT_DOCKED_STATES.discard" not in state

    start = schedule.index("    def _vendor_charging(")
    end = schedule.index("    def _charging_limit_percent(", start)
    charging = schedule[start:end]
    assert "STATE_IDLE_DOCKED_POST" in charging
    assert "mqtt_vehicle_state" not in charging


def test_beta3_location_ordering_precedes_semantic_merge() -> None:
    location = (COMPONENT / "location.py").read_text(encoding="utf-8")
    assert "def _accept_source_order(" in location
    assert '"_source_time_by_type"' in location
    assert '"_late_rejected_by_type"' in location
    assert "if not accepted:" in location
    assert "continue" in location[location.index("if not accepted:"):location.index("if t == 1:")]
    assert '"_state_updated"' in location
    assert '"_action_updated"' in location
    assert '"_partition_ids_updated"' in location
    assert '"_work_target_updated"' in location


def test_beta3_semantic_freshness_uses_acceptance_flags() -> None:
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    navigation = (COMPONENT / "navigation_intent.py").read_text(encoding="utf-8")
    assert 'state_updated = location.get("_state_updated") is True' in coordinator
    assert 'action_updated = location.get("_action_updated") is True' in coordinator
    assert '"_work_target_updated" not in location' in navigation
    assert '"_partition_ids_updated" not in location' in navigation
    assert "raw arrival-order payload contents" in navigation


def test_beta3_diagnostics_expose_ordering_counters_without_raw_payload() -> None:
    mqtt = (COMPONENT / "mqtt.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert "def location_ordering_diagnostics" in mqtt
    assert '"late_rejected_by_type"' in mqtt
    assert '"source_time_by_type"' in mqtt
    assert '"source_ordering": ordering' in diagnostics
