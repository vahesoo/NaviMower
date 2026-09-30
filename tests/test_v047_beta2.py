"""Release contract for Navimower 0.4.7-beta2."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta2_version_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.7-beta2"
    notes = (ROOT / ".github" / "release-notes" / "0.4.7-beta2.md").read_text(
        encoding="utf-8"
    )
    assert notes.startswith("title: Navimower 0.4.7-beta2\n")
    for marker in (
        "scheduler intent now outranks conflicting MQTT noise",
        "currentMowBoundary",
        "MQTT-first private-cloud fallback",
        "navimower.export_raw_data",
    ):
        assert marker in notes


def test_beta2_scheduler_does_not_use_raw_cached_mqtt_as_veto() -> None:
    logic = (COMPONENT / "schedule_logic.py").read_text(encoding="utf-8")
    assert 'cloud_work_zone = _zone_id(data.get("work_target_zone"))' in logic
    assert 'immediate_source == "mqtt_work_target"' in logic
    assert "_ = (mqtt_location, sent_at, handoff_at_send)" in logic
    assert '"state": "zone_mismatch"' not in logic
    assert "currentMowBoundary" in logic


def test_beta2_custom_area_uses_conservative_cloud_fallback() -> None:
    binary = (COMPONENT / "binary_sensor.py").read_text(encoding="utf-8")
    fallback = (COMPONENT / "custom_area_fallback.py").read_text(encoding="utf-8")
    assert "resolve_custom_area_presence" in binary
    assert "_navigation_fallback._position_context" in binary
    assert "_navigation_fallback._cloud_report_time" in binary
    assert '"cloud_fallback": source == "private_cloud"' in binary
    assert "if count >= 2:" in fallback
    assert 'source != "private_cloud"' in fallback


def test_beta2_raw_export_stays_available_on_prerelease() -> None:
    services = (COMPONENT / "services.py").read_text(encoding="utf-8")
    assert 'SERVICE_EXPORT_RAW_DATA = "export_raw_data"' in services
    assert (COMPONENT / "raw_export.py").exists()
