"""Regression tests for Navimower 0.4.4-beta21 rain capability gating."""
from __future__ import annotations

import json
from pathlib import Path

from custom_components.navimower.model_capabilities import (
    FAMILY_I1,
    FAMILY_I2_AWD,
    FAMILY_I2_LIDAR,
    capability_profile,
    model_family,
)

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta21_release_contract() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.4-beta21"

    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta21.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "Rain detection",
        "Rain sensor",
        "Rain forecast",
        "i2 LiDAR",
        "i2 AWD",
        "Georeference behavior from beta20 is unchanged",
    ):
        assert phrase in notes


def test_i1_and_i2_lidar_suppress_split_rain_controls() -> None:
    i1 = capability_profile("I108")
    assert i1.family == FAMILY_I1
    assert i1.rain_detection is False
    assert i1.physical_rain_sensor is False

    i2_lidar = capability_profile("I215 LiDAR")
    assert i2_lidar.family == FAMILY_I2_LIDAR
    assert i2_lidar.rain_detection is False
    assert i2_lidar.physical_rain_sensor is False


def test_i2_awd_remains_unproven_and_unchanged() -> None:
    i2_awd = capability_profile("I2 AWD")
    assert i2_awd.family == FAMILY_I2_AWD
    assert i2_awd.rain_detection is None
    assert i2_awd.physical_rain_sensor is None


def test_i215_lidar_family_detection_accepts_vendor_style_name() -> None:
    assert model_family("I215 lidar") == FAMILY_I2_LIDAR


def test_switch_layer_gates_only_detection_and_physical_sensor() -> None:
    semantics = (COMPONENT / "capability_semantics.py").read_text(encoding="utf-8")
    switch = (COMPONENT / "switch.py").read_text(encoding="utf-8")

    assert 'key == "rain_detection" and family.rain_detection is False' in semantics
    assert 'key == "rain_sensor" and family.physical_rain_sensor is False' in semantics

    # Rain forecast and rain delay stay on their existing shared-schema paths.
    assert 'key="weather_rain"' in switch
    assert 'key="rain_delay_mode"' in switch
    assert 'key="rain_detection"' in switch
    assert 'key="rain_sensor"' in switch
