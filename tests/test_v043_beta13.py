import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta13_identity_and_schedule_modules():
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta13.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta13")
    assert (COMPONENT / "navimower_schedule.py").exists()
    assert (COMPONENT / "schedule_logic.py").exists()


def test_schedule_is_disabled_by_default_and_mutually_exclusive():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    switch = (COMPONENT / "switch.py").read_text(encoding="utf-8")
    assert 'entry.options.get(OPT_SCHEDULE_ENABLED, False)' in source
    assert 'settings.get("schedule_enabled") is True' in source
    assert 'await self._async_set_native_schedule(False)' in source
    assert 'native_schedule_enabled_from_home_assistant' in switch


def test_resume_then_continue_without_automatic_reset_fallback():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    assert 'async_resume_task(self.coordinator, source="navimower_schedule_window_resume")' in source
    assert 'reset=False, source="navimower_schedule_continue_fallback"' in source
    assert 'automatic reset was refused' in source
    assert 'interrupted_task_continue_failed' in source


def test_completion_and_same_zone_race_guards_are_wired():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    assert 'completion_advanced(' in source
    assert 'completed_zone_ids_in_window' in source
    assert 'just_completed_zone_id' in source
    assert 'scheduler_completed_at' in source
    assert 'last_mowed_at' not in source


def test_external_notification_caveat_removed():
    source = (COMPONENT / "notification_center.py").read_text(encoding="utf-8")
    assert "The command source was not Home Assistant" not in source


def test_diagnostics_and_time_controls_are_exposed():
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    time_source = (COMPONENT / "time.py").read_text(encoding="utf-8")
    assert '"navimower_schedule": sanitize' in diagnostics
    assert 'Navimower schedule {key}' in time_source
