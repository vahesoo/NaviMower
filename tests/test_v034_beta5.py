"""Regressions introduced in Navimower v0.3.4-beta5."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_manifest_contains_beta5_feature_line() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.3.4"


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
    assert "enabled_default=False" not in number_source
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
    select_source = (COMPONENT / "select.py").read_text()
    number_source = (COMPONENT / "number.py").read_text()
    assert 'key="rain_delay_time"' in select_source
    assert 'raw_read_key="delayedPileSet"' in select_source
    for label, wire in (
        ("15 min", "01"),
        ("30 min", "02"),
        ("1 h", "04"),
        ("3 h", "0C"),
        ("12 h", "30"),
        ("14 h", "38"),
        ("24 h", "60"),
    ):
        assert f'"{label}": "{wire}"' in select_source
    assert 'key="rain_delay_time"' not in number_source


def test_snow_and_temperature_ranges() -> None:
    source = (COMPONENT / "number.py").read_text()
    snow = source.split('key="snow_delay_time"', 1)[1].split(
        "NavimowNumberDescription(", 1
    )[0]
    assert 'raw_read_key="snowDelayTime"' in snow
    assert "native_unit_of_measurement=UnitOfTime.DAYS" in snow
    assert "native_min_value=1" in snow
    assert "native_max_value=7" in snow
    assert "native_step=1" in snow
    assert "scale=24" in snow
    hot = source.split('key="maximum_mowing_temperature"', 1)[1].split(
        "NavimowNumberDescription(", 1
    )[0]
    assert 'raw_read_key="allowMaxTemp"' in hot
    assert "native_min_value=30" in hot
    assert "native_max_value=45" in hot
    assert "native_step=1" in hot


def test_frost_cutoff_uses_quarter_hour_select() -> None:
    source = (COMPONENT / "select.py").read_text()
    assert "FROST_TIME_VALUES" in source
    assert "range(0, 12 * 60 + 46, 15)" in source
    block = source.split('key="frost_delay_until"', 1)[1].split(
        "NavimowSelectDescription(", 1
    )[0]
    assert 'raw_read_key="frostDelayTime"' in block
    assert 'write_key="frostDelayTime"' in block
    assert "robot_hex=True" in block
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
