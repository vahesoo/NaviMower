"""Regression coverage for 0.4.3-beta57 entity display names."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTITY_SOURCE = ROOT / "custom_components" / "navimower" / "entity.py"

EXPECTED_NAMES = {
    "frost_delay": "Frost detection",
    "frost_delay_until": "Frost delay",
    "high_temp_delay": "Max temp detection",
    "maximum_mowing_temperature": "Max temperature",
    "rain_detection": "Rain detection",
    "rain_sensor": "Rain sensor",
    "weather_rain": "Rain forecast",
    "weather_sensitivity": "Rain forecast sensitivity",
    "rain_delay_mode": "Rain delay",
    "rain_delay_time": "Rain delay duration",
    "snow_delay": "Snow detection",
    "snow_delay_time": "Snow delay",
    "storm_delay": "Wind detection",
    "do_not_disturb": "Do not disturb",
    "quiet_period_start": "Do not disturb start",
    "quiet_period_end": "Do not disturb end",
}


def _entity_name_overrides() -> dict[str, str]:
    module = ast.parse(ENTITY_SOURCE.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "ENTITY_NAME_OVERRIDES":
                value = ast.literal_eval(node.value)
                assert isinstance(value, dict)
                return value
    raise AssertionError("ENTITY_NAME_OVERRIDES not found")


def test_beta57_canonical_weather_and_dnd_names() -> None:
    assert _entity_name_overrides() == EXPECTED_NAMES


def test_beta57_names_do_not_change_unique_id_construction() -> None:
    source = ENTITY_SOURCE.read_text(encoding="utf-8")
    assert 'self._attr_unique_id = f"{self._sn}_{key}"' in source
    assert "ENTITY_NAME_OVERRIDES.get(key)" in source
