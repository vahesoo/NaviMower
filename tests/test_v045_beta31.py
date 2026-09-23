from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
SCHEDULER = COMPONENT / "schedule_dispatch_weather_semantics.py"
MANIFEST = COMPONENT / "manifest.json"


def _source() -> str:
    return SCHEDULER.read_text(encoding="utf-8")


def test_beta31_clears_only_confirmed_docked_weather_pending_command() -> None:
    source = _source()
    start = source.index("def _clear_confirmed_weather_dock_pending(")
    end = source.index("def _external_override_trace(", start)
    helper = source[start:end]

    assert 'pending.get("kind") != "dock"' in helper
    assert 'data.get("activity") != ACTIVITY_DOCKED' in helper
    assert 'controller._runtime["pending_command"] = None' in helper
    assert "ACTIVITY_RETURNING" not in helper


def test_beta31_weather_resume_clears_stale_dock_before_resume_arbitration() -> None:
    source = _source()
    start = source.index(
        'if runtime.get("resume_pending") and interrupted in _WEATHER_REASONS:'
    )
    end = source.index(
        "assert _ORIGINAL_EVALUATE_LOCKED is not None",
        start,
    )
    weather = source[start:end]

    clear_pending = weather.index(
        "if _clear_confirmed_weather_dock_pending(self, data):"
    )
    clear_seen = weather.index('clear_seen = runtime.get("weather_clear_seen_at")')
    resume = weather.index("await self._continue_interrupted_task(")

    assert clear_pending < clear_seen < resume
    assert "await self._save()" in weather[clear_pending:clear_seen]
    assert 'source="navimower_schedule_weather_resume"' in weather
    assert 'continue_source="navimower_schedule_weather_continue_fallback"' in weather


def test_beta31_weather_recovery_still_refuses_reset_mow() -> None:
    source = _source()
    start = source.index(
        'if runtime.get("resume_pending") and interrupted in _WEATHER_REASONS:'
    )
    weather = source[start:]
    assert "reset=True" not in weather


def test_beta31_version() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.5-beta31"
