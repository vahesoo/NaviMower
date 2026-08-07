"""Regression contracts for Navimower 0.4.1-beta1."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_pose_valid_checks_stream_expectation_before_pose_freshness() -> None:
    source = (COMPONENT / "binary_sensor.py").read_text()
    block = source.split('key="pose_valid"', 1)[1].split("NavimowBinaryDescription(", 1)[0]
    assert 'd.get("mqtt_stream_expected") is False' in block
    assert 'True if d.get("mqtt_pose_valid") else False' in block
    assert block.index('d.get("mqtt_stream_expected") is False') < block.index(
        'd.get("mqtt_pose_valid")'
    )
    ast.parse(source)


def test_docked_channel_is_off_before_live_pose_membership() -> None:
    source = (COMPONENT / "binary_sensor.py").read_text()
    block = source.split("class NavimowerChannelBinarySensor", 1)[1].split(
        "class NavimowerGateRequiredBinarySensor", 1
    )[0]
    assert 'self.data.get("docked") is True' in block
    assert "self.coordinator._pending_activity_value() is None" in block
    assert "return False" in block
    assert "return self.coordinator.channel_state(self.channel)" in block
    assert block.index('self.data.get("docked") is True') < block.index(
        "return self.coordinator.channel_state(self.channel)"
    )
    assert "return super().available and self.is_on is not None" in block


def test_private_poll_guard_prevents_mqtt_timer_starvation() -> None:
    source = (COMPONENT / "__init__.py").read_text()
    guard = source.split("async def _async_private_poll_guard", 1)[1].split(
        "async def async_setup", 1
    )[0]
    assert "coordinator.update_interval.total_seconds()" in guard
    assert "age = coordinator.private_poll_age()" in guard
    assert "if age is not None and age < interval * 0.9:" in guard
    assert "await coordinator.async_refresh()" in guard
    assert "await asyncio.sleep(interval)" in guard

    setup = source.split("async def async_setup_entry", 1)[1].split(
        "async def async_unload_entry", 1
    )[0]
    assert "coordinator.private_poll_guard_task = hass.async_create_background_task(" in setup
    assert "_async_private_poll_guard(coordinator)" in setup

    unload = source.split("async def async_unload_entry", 1)[1].split(
        "async def async_remove_entry", 1
    )[0]
    assert "private_poll_guard.cancel()" in unload
    assert "await asyncio.gather(private_poll_guard, return_exceptions=True)" in unload
    assert "if not unload_ok:" in unload
    assert "_async_private_poll_guard(coordinator)" in unload
    ast.parse(source)
