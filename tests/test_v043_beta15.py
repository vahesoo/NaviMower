import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta15_identity():
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta15.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta15")


def test_scheduled_night_resume_resolves_schedule_and_daylight_gate():
    source = (COMPONENT / "notification_center.py").read_text(encoding="utf-8")
    assert "def _scheduled_night_resume_gate" in source
    assert 'return "sunrise", period' in source
    assert 'return "schedule_window", period' in source
    assert 'start_dt < sunrise <= now_local' in source
    assert 'now_local >= sunset' in source
    assert 'kind="scheduled_night_resume"' in source
    assert 'self._active_task["last_resume_gate"] = gate' in source


def test_scheduled_resume_wording_uses_actual_last_gate():
    source = (COMPONENT / "notification_center.py").read_text(encoding="utf-8")
    assert '"Unfinished scheduled mowing resumed after sunrise"' in source
    assert '"Unfinished scheduled mowing resumed"' in source
    assert "daylight was the last remaining gate" in source
    assert "when the scheduled mowing window opened at" in source


def test_retained_non_schedule_task_is_not_gated_by_native_schedule():
    source = (COMPONENT / "notification_center.py").read_text(encoding="utf-8")
    assert "mowing schedule does not gate this resume." in source
    assert "The native mowing schedule does not gate this " in source
    assert "retained task." in source


def test_night_pause_message_explains_native_schedule_gate():
    source = (COMPONENT / "notification_center.py").read_text(encoding="utf-8")
    assert "It can resume when a native mowing window is open and daylight" in source
    assert "effective resume gate" in source
    assert '"native_schedule" if scheduled else "retained_task"' in source
