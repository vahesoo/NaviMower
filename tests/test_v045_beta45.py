"""Release-hardening regressions for Navimower 0.4.5-beta45."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
WEATHER = COMPONENT / "weather_state_semantics.py"
DIAGNOSTICS = COMPONENT / "diagnostics.py"
MANIFEST = COMPONENT / "manifest.json"


def _load_weather_module():
    package_name = "navimower_beta45_test"
    package = types.ModuleType(package_name)
    package.__path__ = [str(COMPONENT)]
    coordinator = types.ModuleType(f"{package_name}.coordinator")
    coordinator.NavimowCoordinator = object
    sys.modules[package_name] = package
    sys.modules[f"{package_name}.coordinator"] = coordinator
    try:
        spec = importlib.util.spec_from_file_location(
            f"{package_name}.weather_state_semantics", WEATHER
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
            "last_rain_state": True,
            "last_rain_state_at": 999_900.0,
            "delay_started_at": None,
            "delay_until": None,
            "delay_minutes": 0,
        }
        self._rain_delay_persist_dirty = False


def _snapshot() -> dict:
    return {
        "settings": {"rain_behavior": True, "rain_delay_wire": 2},
        "raw": {"set_list": {"delayedPileSwitch": 1, "delayedPileSet": "02"}},
    }


def test_beta45_manifest_version() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.5-beta45"


def test_beta45_restored_rain_evidence_starts_delay_on_first_fresh_clear(monkeypatch) -> None:
    module = _load_weather_module()
    coordinator = Coordinator()
    monkeypatch.setattr(module.time, "time", lambda: 1_000_000.0)

    clear = module.normalize_vendor_weather(
        {
            "rainState": 0,
            "snowState": 0,
            "stormState": 0,
            "frostState": 0,
            "highTemperatureState": 0,
        },
        age_s=1.0,
    )
    module._compose_rain_delay(coordinator, _snapshot(), clear)

    assert clear["state"] == "rain_delay"
    assert clear["rain_delay_remaining_minutes"] == 30
    assert clear["rain_last_fresh_state"] is False
    assert coordinator._rain_delay_persist_dirty is True


def test_beta45_persists_last_fresh_rain_state_and_timestamp() -> None:
    source = WEATHER.read_text(encoding="utf-8")
    for marker in (
        '"last_rain_state": runtime.get("last_rain_state")',
        '"last_rain_state_at": runtime.get("last_rain_state_at")',
        "RAIN_EVIDENCE_MAX_AGE_SECONDS = 86_400.0",
        'runtime["last_rain_state_at"] = now',
        'snapshot["rain_last_fresh_state"]',
        'snapshot["rain_last_fresh_state_at"]',
    ):
        assert marker in source


def test_beta45_download_diagnostics_are_curated_not_raw_exports() -> None:
    source = DIAGNOSTICS.read_text(encoding="utf-8")
    for marker in (
        '"profile": "curated_public_support"',
        '"raw_payloads_included": False',
        '"human_labels_included": False',
        '"raw_cache_summary": _raw_cache_summary(raw)',
        '"vendor_notification_raw_cached"',
        '"vendor_notification_normalized_count"',
    ):
        assert marker in source

    for forbidden in (
        '"raw": raw_for_diagnostics',
        '"vendor_notification_raw_cache"',
        '"vendor_notification_normalized_cache"',
        '"raw_index2_error_data"',
        '"title": data.get("notification_title")',
        '"content": data.get("notification_content")',
        '"variable": deepcopy(data.get("notification_variable"))',
    ):
        assert forbidden not in source
