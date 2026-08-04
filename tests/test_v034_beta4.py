"""Regressions for Navimower v0.3.4-beta4."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_manifest_version() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.3.4-beta4"


def test_map_edit_state_codes_share_one_label_and_paused_activity() -> None:
    source = (COMPONENT / "const.py").read_text()
    assert 'STATE_MAP_EDIT: Final = "0202"' in source
    assert 'STATE_MAP_EDIT_MANUAL: Final = "0258"' in source
    assert 'STATE_MAP_EDIT: ACTIVITY_PAUSED' in source
    assert 'STATE_MAP_EDIT_MANUAL: ACTIVITY_PAUSED' in source
    assert source.count('"Map edit"') == 2
    assert 'MAP_EDIT_STATES: Final = {STATE_MAP_EDIT, STATE_MAP_EDIT_MANUAL}' in source


def test_map_edit_does_not_claim_docked_and_is_visible_on_mower() -> None:
    binary_source = (COMPONENT / "binary_sensor.py").read_text()
    mower_source = (COMPONENT / "lawn_mower.py").read_text()
    assert 'in MAP_EDIT_STATES' in binary_source
    assert '"map_editing": state_code in MAP_EDIT_STATES' in mower_source


def test_animal_and_night_light_read_cloud_state() -> None:
    source = (COMPONENT / "switch.py").read_text()
    assert 'raw_read_key="animalProtection"' in source
    assert 'raw_read_key="lightSwitch"' in source
    assert 'key="animal_protection"' in source
    assert 'key="night_light"' in source
    animal_block = source.split('key="animal_protection"', 1)[1].split(
        'NavimowSwitchDescription(', 1
    )[0]
    night_block = source.split('key="night_light"', 1)[1].split(
        'NavimowSwitchDescription(', 1
    )[0]
    assert "assumed=True" not in animal_block
    assert "assumed=True" not in night_block
    ast.parse(source)


def test_work_mode_mapping_and_dual_write() -> None:
    source = (COMPONENT / "select.py").read_text()
    assert 'key="work_mode"' in source
    assert 'raw_read_key="mode"' in source
    assert '"standard": "02"' in source
    assert '"efficient": "03"' in source
    assert '"precision": "04"' in source
    assert "send_setting_device" in source
    assert "save_setting_iot" in source


def test_geo_fence_radius_is_opt_in_and_center_is_not_exposed() -> None:
    source = (COMPONENT / "number.py").read_text()
    block = source.split('key="geo_fence_radius"', 1)[1].split(
        'NavimowNumberDescription(', 1
    )[0]
    assert 'raw_read_key="antiTheftRadius"' in block
    assert "native_min_value=10" in block
    assert "native_max_value=50" in block
    assert "native_step=10" in block
    assert "enabled_default=False" in block
    assert "antiTheftPoint" not in source
