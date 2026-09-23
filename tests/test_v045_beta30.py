from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
WEATHER = COMPONENT / "weather_state_semantics.py"
SENSOR = COMPONENT / "sensor.py"
SCHEDULER = COMPONENT / "schedule_dispatch_weather_semantics.py"
MANIFEST = COMPONENT / "manifest.json"


def _load_weather_module():
    package_name = "navimower_beta30_test"
    package = types.ModuleType(package_name)
    package.__path__ = [str(COMPONENT)]
    coordinator = types.ModuleType(f"{package_name}.coordinator")
    coordinator.NavimowCoordinator = object
    sys.modules[package_name] = package
    sys.modules[f"{package_name}.coordinator"] = coordinator
    try:
        spec = importlib.util.spec_from_file_location(
            f"{package_name}.weather_state_semantics",
            WEATHER,
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(f"{package_name}.coordinator", None)
        sys.modules.pop(package_name, None)


class Coordinator:
    def __init__(self) -> None:
        self._rain_delay_runtime = {
            "last_rain_state": None,
            "delay_started_at": None,
            "delay_until": None,
            "delay_minutes": 0,
        }
        self._rain_delay_persist_dirty = False


def _snapshot(*, enabled: bool = True, wire: str = "02") -> dict:
    return {
        "settings": {
            "rain_behavior": enabled,
            "rain_delay_wire": int(wire, 16),
        },
        "raw": {
            "set_list": {
                "delayedPileSwitch": 1 if enabled else 0,
                "delayedPileSet": wire,
            }
        },
    }


def test_beta30_rain_state_one_is_raining_not_rain_delay() -> None:
    module = _load_weather_module()
    weather = module.normalize_vendor_weather(
        {
            "rainState": 1,
            "rainLevel": 2,
            "snowState": 0,
            "stormState": 0,
            "frostState": 0,
            "highTemperatureState": 0,
        },
        age_s=1.0,
    )
    assert weather["state"] == "raining"
    assert weather["label"] == "Raining"
    assert weather["hold_reason"] == "rain"


def test_beta30_one_to_zero_starts_configured_30_minute_delay(monkeypatch) -> None:
    module = _load_weather_module()
    coordinator = Coordinator()
    monkeypatch.setattr(module.time, "time", lambda: 1_000_000.0)

    raining = module.normalize_vendor_weather(
        {"rainState": 1, "snowState": 0, "stormState": 0, "frostState": 0, "highTemperatureState": 0},
        age_s=1.0,
    )
    module._compose_rain_delay(coordinator, _snapshot(), raining)
    assert raining["state"] == "raining"

    clear = module.normalize_vendor_weather(
        {"rainState": 0, "snowState": 0, "stormState": 0, "frostState": 0, "highTemperatureState": 0},
        age_s=1.0,
    )
    module._compose_rain_delay(coordinator, _snapshot(), clear)

    assert clear["state"] == "rain_delay"
    assert clear["label"] == "Rain delay"
    assert clear["hold_active"] is True
    assert clear["hold_reason"] == "rain_delay"
    assert clear["rain_delay_configured_minutes"] == 30
    assert clear["rain_delay_remaining_minutes"] == 30
    assert clear["rain_delay_remaining_seconds"] == 1800
    assert coordinator._rain_delay_persist_dirty is True


def test_beta30_countdown_uses_ceil_minutes_and_expires(monkeypatch) -> None:
    module = _load_weather_module()
    coordinator = Coordinator()
    coordinator._rain_delay_runtime.update(
        {
            "last_rain_state": False,
            "delay_started_at": 1_000_000.0,
            "delay_until": 1_001_800.0,
            "delay_minutes": 30,
        }
    )
    weather = module.normalize_vendor_weather(
        {"rainState": 0, "snowState": 0, "stormState": 0, "frostState": 0, "highTemperatureState": 0},
        age_s=1.0,
    )

    monkeypatch.setattr(module.time, "time", lambda: 1_000_061.0)
    module._compose_rain_delay(coordinator, _snapshot(), weather)
    assert weather["rain_delay_remaining_minutes"] == 29

    weather = module.normalize_vendor_weather(
        {"rainState": 0, "snowState": 0, "stormState": 0, "frostState": 0, "highTemperatureState": 0},
        age_s=1.0,
    )
    monkeypatch.setattr(module.time, "time", lambda: 1_001_800.0)
    module._compose_rain_delay(coordinator, _snapshot(), weather)
    assert weather["state"] == "clear"
    assert weather["hold_active"] is False
    assert weather["rain_delay_remaining_minutes"] == 0


def test_beta30_new_rain_cancels_existing_delay(monkeypatch) -> None:
    module = _load_weather_module()
    coordinator = Coordinator()
    coordinator._rain_delay_runtime.update(
        {
            "last_rain_state": False,
            "delay_started_at": 1_000_000.0,
            "delay_until": 1_001_800.0,
            "delay_minutes": 30,
        }
    )
    monkeypatch.setattr(module.time, "time", lambda: 1_000_300.0)
    weather = module.normalize_vendor_weather(
        {"rainState": 1, "snowState": 0, "stormState": 0, "frostState": 0, "highTemperatureState": 0},
        age_s=1.0,
    )
    module._compose_rain_delay(coordinator, _snapshot(), weather)
    assert weather["state"] == "raining"
    assert weather["rain_delay_active"] is False
    assert weather["rain_delay_remaining_minutes"] == 0


def test_beta30_stale_clear_does_not_create_transition(monkeypatch) -> None:
    module = _load_weather_module()
    coordinator = Coordinator()
    coordinator._rain_delay_runtime["last_rain_state"] = True
    monkeypatch.setattr(module.time, "time", lambda: 1_000_000.0)
    weather = module.normalize_vendor_weather(
        {"rainState": 0, "snowState": 0, "stormState": 0, "frostState": 0, "highTemperatureState": 0},
        age_s=999.0,
    )
    module._compose_rain_delay(coordinator, _snapshot(), weather)
    assert weather["state"] == "clear"
    assert weather["rain_delay_active"] is False


def test_beta30_public_sensor_and_scheduler_use_same_hold() -> None:
    sensor = SENSOR.read_text(encoding="utf-8")
    scheduler = SCHEDULER.read_text(encoding="utf-8")
    assert 'key="rain_delay_remaining"' in sensor
    assert 'name="Rain delay remaining"' in sensor
    assert "UnitOfTime.MINUTES" in sensor
    assert '"rain_delay"' in scheduler
    assert 'data.get("weather_hold_active")' in scheduler


def test_beta30_version() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.5-beta30"
