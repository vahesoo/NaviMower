"""Regressions for Navimower v0.3.4-beta8 and later releases."""
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
    assert manifest["version"]


def test_snow_delay_is_exposed_as_one_to_seven_days() -> None:
    source = (COMPONENT / "number.py").read_text()
    block = _description_block(source, "snow_delay_time", "NavimowNumberDescription(")
    assert "native_unit_of_measurement=UnitOfTime.DAYS" in block
    assert "native_min_value=1" in block
    assert "native_max_value=7" in block
    assert "native_step=1" in block
    assert "scale=24" in block
    assert 'raw_read_key="snowDelayTime"' in block
    assert "robot_hex=True" in block
    assert 1 * 24 == 24
    assert 7 * 24 == 168
    assert f"{3 * 24:02X}" == "48"


def test_night_mowing_uses_nested_read_and_legacy_write() -> None:
    source = (COMPONENT / "switch.py").read_text()
    block = _description_block(source, "night_mow", "NavimowSwitchDescription(")
    assert 'name="Night mowing"' in block
    assert "entity_category=EntityCategory.CONFIG" not in block
    assert 'write_key="nightMowSwitch"' in block
    assert 'raw_read_path=("camerabox", "nightMowSwitch")' in block
    assert 'raw_fallback_keys=("nightMowSwitch", "night_mow_switch")' in block
    assert "iot=True" not in block
    assert "set_bool_setting" in source
    assert "_nested_cache_root(" in source
    assert "cache_values[root_key] = root" in source


def test_do_not_disturb_and_quiet_period_mapping() -> None:
    switch_source = (COMPONENT / "switch.py").read_text()
    select_source = (COMPONENT / "select.py").read_text()

    dnd = _description_block(
        switch_source, "do_not_disturb", "NavimowSwitchDescription("
    )
    assert 'raw_read_key="dndModeSwitch"' in dnd
    assert 'write_key="dndModeSwitch"' in dnd
    assert "robot_numeric=False" in dnd
    assert "numeric=False" in dnd

    for key, index in (("quiet_period_start", 0), ("quiet_period_end", 1)):
        block = _description_block(select_source, key, "NavimowSelectDescription(")
        assert 'raw_read_key="dndPeriod"' in block
        assert 'write_key="dndPeriod"' in block
        assert "value_map=DAY_TIME_VALUES" in block
        assert f"compound_index={index}" in block

    assert "range(0, 24 * 60, 15)" in select_source
    assert 'compound = f"{updated[0]:02X}{updated[1]:02X}"' in select_source
    assert int("4C", 16) * 15 // 60 == 19
    assert int("20", 16) * 15 // 60 == 8


def test_energy_saver_and_model_specific_brightness() -> None:
    switch_source = (COMPONENT / "switch.py").read_text()
    select_source = (COMPONENT / "select.py").read_text()

    energy = _description_block(
        switch_source, "power_saving", "NavimowSwitchDescription("
    )
    assert 'name="Energy saver"' in energy
    assert 'write_key="lowPowerSet"' in energy
    assert "numeric=True" in energy

    h215 = _description_block(
        select_source, "night_light_level", "NavimowSelectDescription("
    )
    assert 'name="Night light brightness"' in h215
    assert 'raw_read_key="lightIntensity"' in h215
    assert 'write_key="lightIntensity"' in h215
    assert '"Default": "0"' in h215
    assert '"Dim": "1"' in h215
    assert '"Extra dim": "2"' in h215
    assert 'models=("H215",)' in h215

    x390 = _description_block(
        select_source, "light_brightness", "NavimowSelectDescription("
    )
    assert 'name="Brightness"' in x390
    assert 'raw_read_key="nightLightLevel"' in x390
    assert 'write_key="nightLightLevel"' in x390
    assert '"Dim": 0' in x390
    assert '"Extra dim": 1' in x390
    assert 'models=("X390",)' in x390


def test_h215_lab_controls_are_raw_key_and_model_gated() -> None:
    switch_source = (COMPONENT / "switch.py").read_text()
    select_source = (COMPONENT / "select.py").read_text()

    terrain = _description_block(
        switch_source, "terrain_adapt", "NavimowSwitchDescription("
    )
    assert 'raw_read_key="terrainAdaptSwitch"' in terrain
    assert 'write_key="terrainAdaptSwitch"' in terrain
    assert "numeric=True" in terrain
    assert 'models=("H215",)' in terrain

    edge = _description_block(
        switch_source, "edge_sense", "NavimowSwitchDescription("
    )
    assert 'raw_read_key="edgeSense"' in edge
    assert 'write_key="edgeSense"' in edge
    assert "numeric=True" in edge
    assert 'models=("H215",)' in edge

    level = _description_block(
        select_source, "edge_sense_mode", "NavimowSelectDescription("
    )
    assert 'raw_read_key="edgeSenselevel"' in level
    assert 'write_key="edgeSenselevel"' in level
    assert '"Standard": 0' in level
    assert '"Cautious": 1' in level
    assert '"Extreme": 2' in level
    assert "robot_numeric=True" in level
    assert 'models=("H215",)' in level


def test_beta8_setting_sources_compile() -> None:
    for filename in ("switch.py", "select.py", "number.py"):
        ast.parse((COMPONENT / filename).read_text())
