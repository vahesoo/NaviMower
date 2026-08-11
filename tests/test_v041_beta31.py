"""Regression contracts for Navimower 0.4.1-beta31."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _description_block(source: str, key: str) -> str:
    marker = f'key="{key}"'
    start = source.index("NavimowSwitchDescription(", source.index(marker) - 80)
    end = source.index("    ),", source.index(marker)) + 6
    return source[start:end]


def test_beta31_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta31"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta31.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta31")
    for phrase in (
        "Night mowing",
        "Rain detection",
        "Rain sensor",
        "robot-first",
        '"00"/"01"',
        "field validation",
    ):
        assert phrase in notes


def test_beta31_legacy_switches_request_device_write() -> None:
    source = (COMPONENT / "switch.py").read_text()
    ast.parse(source)
    assert "legacy_device_write: bool = False" in source
    for key in ("night_mow", "rain_detection", "rain_sensor"):
        block = _description_block(source, key)
        assert "legacy_device_write=True" in block
        assert "iot=True" not in block


def test_beta31_legacy_path_is_robot_first_cloud_second() -> None:
    source = (COMPONENT / "switch.py").read_text()
    write = source[source.index("    async def _write"):source.index("    async def async_turn_on")]
    legacy = write[write.index("        else:"):]
    assert 'cloud_value = "01" if on else "00"' in legacy
    assert "if desc.legacy_device_write:" in legacy
    assert "self.coordinator.client.send_setting_device" in legacy
    assert "self.coordinator.client.set_bool_setting" in legacy
    assert legacy.index("self.coordinator.client.send_setting_device") < legacy.index(
        "self.coordinator.client.set_bool_setting"
    )
    assert "self.coordinator.client.set_iot_bool" not in legacy


def test_beta31_legacy_device_value_is_numeric_by_default() -> None:
    source = (COMPONENT / "switch.py").read_text()
    write = source[source.index("    async def _write"):source.index("    async def async_turn_on")]
    legacy = write[write.index("        else:"):]
    assert "if desc.robot_numeric" in legacy
    assert "(1 if on else 0)" in legacy
    assert "desc.robot_key or desc.write_key" in legacy


def test_beta31_night_mow_keeps_nested_read_contract() -> None:
    source = (COMPONENT / "switch.py").read_text()
    block = _description_block(source, "night_mow")
    assert 'raw_read_path=("camerabox", "nightMowSwitch")' in block
    assert 'raw_fallback_keys=("nightMowSwitch", "night_mow_switch")' in block
