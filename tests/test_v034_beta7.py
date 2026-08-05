"""Regressions for Navimower v0.3.4-beta7."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _description_block(source: str, key: str, marker: str) -> str:
    return source.split(f'key="{key}"', 1)[1].split(marker, 1)[0]


def test_manifest_version() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.3.4-beta7"


def test_discrete_weather_values_use_selects() -> None:
    select_source = (COMPONENT / "select.py").read_text()
    number_source = (COMPONENT / "number.py").read_text()

    rain = _description_block(
        select_source, "rain_delay_time", "NavimowSelectDescription("
    )
    assert 'name="Wait time after rain"' in rain
    assert 'raw_read_key="delayedPileSet"' in rain
    assert "value_map=RAIN_DELAY_VALUES" in rain
    assert 'key="rain_delay_time"' not in number_source

    for label, wire in (
        ("15 min", "01"),
        ("30 min", "02"),
        ("1 h", "04"),
        ("2 h", "08"),
        ("3 h", "0C"),
        ("12 h", "30"),
        ("14 h", "38"),
        ("24 h", "60"),
    ):
        assert f'"{label}": "{wire}"' in select_source
    assert "1.25" not in select_source


def test_frost_select_is_quarter_hour_only_and_hex_to_robot() -> None:
    source = (COMPONENT / "select.py").read_text()
    block = _description_block(
        source, "frost_delay_until", "NavimowSelectDescription("
    )
    assert "range(0, 12 * 60 + 46, 15)" in source
    assert 'raw_read_key="frostDelayTime"' in block
    assert "value_map=FROST_TIME_VALUES" in block
    assert "robot_hex=True" in block
    assert 'robot_value: int | str = f"{int(value):02X}"' in source
    assert "cloud_value = value" in source

    legacy = (COMPONENT / "time.py").read_text()
    assert "NavimowFrostTime" not in legacy
    assert "Do not create legacy time entities" in legacy


def test_continuous_numeric_settings_are_sliders_with_hex_robot_values() -> None:
    source = (COMPONENT / "number.py").read_text()
    for key, raw_key, minimum, maximum in (
        ("snow_delay_time", "snowDelayTime", 24, 168),
        ("maximum_mowing_temperature", "allowMaxTemp", 30, 45),
        ("geo_fence_radius", "antiTheftRadius", 10, 50),
    ):
        block = _description_block(source, key, "NavimowNumberDescription(")
        assert f'raw_read_key="{raw_key}"' in block
        assert f"native_min_value={minimum}" in block
        assert f"native_max_value={maximum}" in block
        assert "native_step=1" in block
        assert "mode=NumberMode.SLIDER" in block
        assert "robot_hex=True" in block

    geo = _description_block(source, "geo_fence_radius", "NavimowNumberDescription(")
    assert "cloud_string=True" in geo
    assert 'robot_value: int | str = f"{wire:02X}"' in source
    ast.parse(source)


def test_live_capture_examples_match_hex_encoding() -> None:
    assert f"{20:02X}" == "14"  # Geo-fence: 20 m, not decimal text "20" (=0x20/32).
    assert f"{25:02X}" == "19"  # Geo-fence: 25 m, not decimal text "25" (=0x25/37).
    assert f"{36:02X}" == "24"  # Snow delay: 36 h, not decimal text "36" (=0x36/54).
    assert f"{28:02X}" == "1C"  # Frost 07:00: 28 quarter-hours, not 0x28/10:00.
    assert f"{31:02X}" == "1F"  # Temperature 31 C, not decimal text "31" (=0x31/49).
