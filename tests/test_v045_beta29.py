from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
PAUSE = COMPONENT / "mowing_pause_status.py"
SCHEDULER = COMPONENT / "schedule_dispatch_weather_semantics.py"
DIAGNOSTICS = COMPONENT / "diagnostics.py"
MANIFEST = COMPONENT / "manifest.json"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_beta29_mowing_pause_prefers_fresh_vendor_weather() -> None:
    source = _source(PAUSE)
    assert 'weather_active = weather_fresh and weather.get("hold_active") is True' in source
    assert 'confidence = "vendor_weather_state"' in source
    assert '"underlying_interrupted_reason": transition_reason' in source
    assert '"automation_safe_weather": weather_active' in source


def test_beta29_stale_weather_does_not_override_pause_reason() -> None:
    source = _source(PAUSE)
    assert 'weather_fresh = weather.get("fresh") is True' in source
    assert "a stale weather row must never hide" in source


def test_beta29_scheduler_restarts_clear_grace_when_weather_returns() -> None:
    source = _source(SCHEDULER)
    assert 'if runtime.get("weather_clear_seen_at") is not None:' in source
    assert 'runtime["weather_clear_seen_at"] = None' in source
    assert "A later real clear must earn a fresh" in source
    assert "if _set_weather_interruption(" in source


def test_beta29_scheduler_refines_generic_task_delay_to_direct_weather_reason() -> None:
    source = _source(SCHEDULER)
    assert "if reason in _DIRECT_WEATHER_REASONS and current_reason != reason:" in source
    assert 'runtime["interrupted_reason"] = reason' in source


def test_beta29_diagnostics_exposes_composed_weather_state() -> None:
    source = _source(DIAGNOSTICS)
    assert '"display_state"' in source
    assert '"weather_state"' in source
    assert '"weather_hold_reason"' in source
    assert '"weather_state_fresh"' in source


def test_beta29_version_floor() -> None:
    manifest = json.loads(_source(MANIFEST))
    version = str(manifest["version"])
    assert version.startswith("0.4.5-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 29
