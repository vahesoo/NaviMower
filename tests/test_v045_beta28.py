from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
WEATHER = COMPONENT / "weather_state_semantics.py"
SCHEDULER = COMPONENT / "schedule_dispatch_weather_semantics.py"
SENSOR = COMPONENT / "sensor.py"
MOWER = COMPONENT / "lawn_mower.py"
RUNTIME = COMPONENT / "runtime.py"
RAW_EXPORT = COMPONENT / "raw_export.py"
STATUS = COMPONENT / "schedule_status.py"
MANIFEST = COMPONENT / "manifest.json"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_beta28_polls_vendor_weather_decision_endpoint() -> None:
    source = _source(WEATHER)
    assert 'VENDOR_WEATHER_PATH = "/vehicle/vehicle/get-vehicle-weather"' in source
    for field in (
        "rainState",
        "snowState",
        "stormState",
        "frostState",
        "highTemperatureState",
        "rainLevel",
    ):
        assert field in source
    assert '"vehicle_weather"' in source
    assert '"private_cloud_vehicle_weather"' in source


def test_beta28_preserves_unknown_vendor_weather_values() -> None:
    source = _source(WEATHER)
    assert "0 = inactive and 1 = active" in source
    assert 'return None' in source
    assert '"unknown_reasons"' in source
    assert 'weather["fresh"]' in source


def test_beta28_runtime_installs_weather_before_scheduler() -> None:
    source = _source(RUNTIME)
    assert "install_weather_state_semantics" in source
    weather = source.index("install_weather_state_semantics()")
    dispatch = source.index("install_schedule_dispatch_weather_semantics()")
    assert weather < dispatch


def test_beta28_exposes_weather_and_composed_status_entities() -> None:
    sensor = _source(SENSOR)
    mower = _source(MOWER)
    assert 'key="weather_state"' in sensor
    assert 'name="Weather state"' in sensor
    assert 'd.get("display_state") or d.get("state")' in sensor
    assert '"weather_hold_reason"' in sensor
    assert '"display_state": data.get("display_state") or data.get("state")' in mower
    assert '"weather_rain": data.get("weather_rain_state")' in mower


def test_beta28_scheduler_blocks_new_dispatch_on_fresh_weather_hold() -> None:
    source = _source(SCHEDULER)
    assert "def _direct_weather_decision(" in source
    assert 'data.get("weather_state_fresh") is not True' in source
    assert 'runtime["weather_dispatch_hold_reason"] = reason' in source
    assert 'runtime["last_command"] = f"weather_dispatch_hold:{reason}"' in source
    assert 'runtime["last_command"] = f"weather_dispatch_clear:{dispatch_hold}"' in source
    assert "A hold must be released only by a fresh explicit clear decision." in source


def test_beta28_keeps_legacy_task_delay_weather_recovery_fallback() -> None:
    source = _source(SCHEDULER)
    assert 'getter("task_delay")' in source
    assert '"150A": "rain"' in source
    assert '"150F": "snow"' in source
    assert "_WEATHER_AUTO_RESUME_GRACE_SECONDS = 120.0" in source


def test_beta28_fresh_clear_releases_known_direct_weather_interruption() -> None:
    source = _source(SCHEDULER)
    assert "if direct_active is False:" in source
    assert "if interrupted in _DIRECT_WEATHER_REASONS:" in source
    assert "Do not let an older MQTT taskDelay keep Rain/Snow/etc." in source


def test_beta28_schedule_status_reports_weather_delay() -> None:
    source = _source(STATUS)
    assert 'state = "weather_delay"' in source
    assert '"weather_delay_reason": weather_reason' in source
    assert '"weather_state": (controller.coordinator.data or {}).get("weather_state")' in source


def test_beta28_raw_export_includes_live_weather_endpoint() -> None:
    source = _source(RAW_EXPORT)
    assert '"vehicle_weather"' in source
    assert '"/vehicle/vehicle/get-vehicle-weather"' in source


def test_beta28_version_floor() -> None:
    manifest = json.loads(_source(MANIFEST))
    version = str(manifest["version"])
    assert version.startswith("0.4.5-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 28
