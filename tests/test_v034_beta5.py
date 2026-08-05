"""Regressions introduced in Navimower v0.3.4-beta5."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_manifest_is_at_least_beta5_feature_line() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"].startswith("0.3.4-beta")


def test_geo_fence_alarm_and_radius_are_cloud_backed() -> None:
    switch_source = (COMPONENT / "switch.py").read_text()
    number_source = (COMPONENT / "number.py").read_text()
    assert 'key="geo_fence_alarm"' in switch_source
    assert 'raw_read_key="guard"' in switch_source
    assert 'write_key="guard"' in switch_source
    assert "robot_numeric=False" in switch_source
    assert 'key="geo_fence_radius"' in number_source
    assert 'raw_read_key="antiTheftRadius"' in number_source
    assert "native_min_value=10" in number_source
    assert "native_max_value=50" in number_source
    assert "native_step=1" in number_source
    assert "enabled_default=False" in number_source
    assert "antiTheftPoint" not in switch_source + number_source


def test_weather_switches_match_app_meaning() -> None:
    source = (COMPONENT / "switch.py").read_text()
    for key, wire in (
        ("rain_detection", "rainDetectionSwitch"),
        ("rain_sensor", "rainSensor"),
        ("weather_rain", "weatherSwitch"),
        ("rain_delay_mode", "delayedPileSwitch"),
        ("frost_delay", "frostSwitch"),
        ("snow_delay", "snowSwitch"),
        ("storm_delay", "stormSwitch"),
        ("high_temp_delay", "highTempSwitch"),
    ):
        assert f'key="{key}"' in source
        assert f'write_key="{wire}"' in source
    assert source.count("entity_category=EntityCategory.CONFIG") >= 20


def test_rain_wait_options_and_hex_wire_encoding() -> None:
    source = (COMPONENT / "number.py").read_text()
    assert "RAIN_WAIT_HOURS" in source
    for value in ("0.25", "0.5", "12", "14", "16", "18", "20", "22", "24"):
        assert value in source
    block = source.split('key="rain_delay_time"', 1)[1].split(
        "NavimowNumberDescription(", 1
    )[0]
    assert 'raw_read_key="delayedPileSet"' in block
    assert "raw_base=16" in block
    assert "scale=4" in block
    assert "cloud_hex=True" in block
    assert "allowed_native_values=RAIN_WAIT_HOURS" in block
    assert 'f"{wire:02X}"' in source
    assert "wire = int(round(native * desc.scale))" in source


def test_snow_and_temperature_ranges() -> None:
    source = (COMPONENT / "number.py").read_text()
    snow = source.split('key="snow_delay_time"', 1)[1].split(
        "NavimowNumberDescription(", 1
    )[0]
    assert 'raw_read_key="snowDelayTime"' in snow
    assert "native_min_value=24" in snow
    assert "native_max_value=168" in snow
    assert "native_step=1" in snow
    hot = source.split('key="maximum_mowing_temperature"', 1)[1].split(
        "NavimowNumberDescription(", 1
    )[0]
    assert 'raw_read_key="allowMaxTemp"' in hot
    assert "native_min_value=30" in hot
    assert "native_max_value=45" in hot
    assert "native_step=1" in hot


def test_frost_time_platform_uses_quarter_hours() -> None:
    init_source = (COMPONENT / "__init__.py").read_text()
    source = (COMPONENT / "time.py").read_text()
    assert "Platform.TIME" in init_source
    assert "FROST_TIME_STEP_MINUTES = 15" in source
    assert "FROST_TIME_MAX_MINUTES = 12 * 60 + 45" in source
    assert '"frostDelayTime": wire' in source
    assert "send_setting_device" in source
    assert "save_setting_iot" in source
    ast.parse(source)


def test_translation_files_match_and_include_beta5_entities() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text())
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    assert strings == english
    entity = strings["entity"]
    assert entity["switch"]["rain_detection"]["name"] == "Rain"
    assert entity["switch"]["rain_delay_mode"]["name"] == "Delay after rain"
    assert entity["switch"]["geo_fence_alarm"]["name"] == "Geo-fence alarm"
    assert entity["number"]["snow_delay_time"]["name"] == "Snow delay duration"
    assert entity["number"]["maximum_mowing_temperature"]["name"] == "Maximum mowing temperature"
    assert entity["time"]["frost_delay_until"]["name"] == "Won't mow until after frost"
