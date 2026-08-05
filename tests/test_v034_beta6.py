"""Regressions for Navimower v0.3.4-beta6."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_manifest_version() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.3.4-beta7"


def test_setting_transaction_has_one_executor_job_and_delayed_readback() -> None:
    source = (COMPONENT / "setting_write.py").read_text()
    assert "SETTING_READBACK_DELAY_SECONDS = 15.0" in source
    assert "async_add_executor_job(_run_operations)" in source
    assert "set_list.update(cache_values)" in source
    assert "await asyncio.sleep(delay)" in source
    assert 'status["last_attempt_mono"] = None' in source
    assert "await coordinator.async_request_refresh()" in source
    assert "coordinator.async_set_updated_data(snapshot)" in source
    ast.parse(source)


def test_all_active_setting_platforms_use_shared_transaction() -> None:
    for filename in ("switch.py", "select.py", "number.py"):
        source = (COMPONENT / filename).read_text()
        assert "from .setting_write import async_write_settings" in source
        assert "await async_write_settings(" in source
        ast.parse(source)


def test_switches_no_longer_refresh_between_robot_and_cloud_writes() -> None:
    source = (COMPONENT / "switch.py").read_text()
    write_block = source.split("    async def _write(self, on: bool) -> None:", 1)[1]
    write_block = write_block.split("    async def async_turn_on", 1)[0]
    assert "send_setting_device" in write_block
    assert "set_iot_bool" in write_block
    assert "self.coordinator.async_send" not in write_block
    assert "cache_values={desc.write_key: cloud_value}" in write_block


def test_value_entities_use_acknowledged_write_through_values() -> None:
    select_source = (COMPONENT / "select.py").read_text()
    number_source = (COMPONENT / "number.py").read_text()
    assert "cache_values={key: cloud_value}" in select_source
    assert "cache_values={key: cloud_value}" in number_source


def test_legacy_time_platform_creates_no_duplicate_frost_entity() -> None:
    source = (COMPONENT / "time.py").read_text()
    assert "Do not create legacy time entities" in source
    assert "NavimowFrostTime" not in source
    ast.parse(source)


def test_geo_fence_alarm_uses_radar_icon() -> None:
    source = (COMPONENT / "switch.py").read_text()
    block = source.split('key="geo_fence_alarm"', 1)[1].split(
        "NavimowSwitchDescription(", 1
    )[0]
    assert 'icon="mdi:radar"' in block
