from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

ROOT = Path.cwd()
COMPONENT = ROOT / "custom_components" / "navimower"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one marker, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).lstrip(), encoding="utf-8")


manifest_path = COMPONENT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != "0.4.3-beta14":
    raise SystemExit(f"Expected beta14 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta15"
manifest_path.write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)

notifications_path = COMPONENT / "notification_center.py"

old_night_resume = '''        if self._active_task is not None and self._interrupted_reason == "night":
            sunrise = self._sun_event_local(SUN_EVENT_SUNRISE, now_local.date())
            if sunrise is not None and sunrise <= now_local <= sunrise + timedelta(
                hours=_SUNRISE_RESUME_WINDOW_HOURS
            ):
                names = list(self._active_task.get("zone_names") or [])
                content = "Resumed the unfinished mowing task after sunrise"
                if names:
                    content += f" in {_zone_phrase(names)}"
                content += "."
                if self._active_task.get("origin") != "schedule":
                    next_start = self._next_schedule_start_today(snapshot, now_local)
                    if next_start:
                        content += (
                            " This is a continuation of the previous task; today's "
                            f"scheduled mowing starts at {next_start}."
                        )
                    else:
                        content += " This is a continuation of the previous task."
                item = self._emit(
                    "NM1005",
                    "Unfinished mowing resumed after sunrise",
                    content,
                    kind="sunrise_resume",
                    confidence="inferred_from_retained_task_and_sunrise",
                    event_key=f"{self._task_token()}-{now_local.date().isoformat()}",
                )
                self._active_task["last_resumed_at"] = datetime.now(UTC).isoformat()
                self._interrupted_reason = None
                return item is not None
'''
new_night_resume = '''        if self._active_task is not None and self._interrupted_reason == "night":
            names = list(self._active_task.get("zone_names") or [])
            origin = str(self._active_task.get("origin") or "")
            sunrise = self._sun_event_local(SUN_EVENT_SUNRISE, now_local.date())

            if origin == "schedule":
                resolved = self._scheduled_night_resume_gate(snapshot, now_local)
                if resolved is None:
                    # A retained native-schedule task must not be attributed to
                    # sunrise alone. Wait until a real schedule/daylight gate is
                    # observable instead of inventing an External start.
                    return False
                gate, period = resolved
                start_dt = period["start_dt"]
                end_dt = period["end_dt"]
                if gate == "sunrise":
                    content = "Resumed the unfinished scheduled mowing task after sunrise"
                    if names:
                        content += f" in {_zone_phrase(names)}"
                    content += (
                        f". The scheduled mowing window opened at {start_dt.strftime('%H:%M')}, "
                        "but Night mowing is disabled, so daylight was the last remaining gate."
                    )
                    if sunrise is not None:
                        content += f" Sunrise was around {sunrise.strftime('%H:%M')}."
                    title = "Unfinished scheduled mowing resumed after sunrise"
                else:
                    content = "Resumed the unfinished scheduled mowing task"
                    if names:
                        content += f" in {_zone_phrase(names)}"
                    content += (
                        f" when the scheduled mowing window opened at {start_dt.strftime('%H:%M')}."
                    )
                    if sunrise is not None and sunrise <= start_dt:
                        content += (
                            f" Daylight was already available after sunrise around "
                            f"{sunrise.strftime('%H:%M')}."
                        )
                    title = "Unfinished scheduled mowing resumed"

                item = self._emit(
                    "NM1005",
                    title,
                    content,
                    kind="scheduled_night_resume",
                    confidence="inferred_from_retained_schedule_and_daylight_gate",
                    event_key=(
                        f"{self._task_token()}-{now_local.date().isoformat()}-"
                        f"{period['start_min']}-{gate}"
                    ),
                )
                self._active_task["schedule_start"] = start_dt.isoformat()
                self._active_task["schedule_end"] = end_dt.isoformat()
                self._active_task["last_resume_gate"] = gate
                self._active_task["last_resumed_at"] = datetime.now(UTC).isoformat()
                self._interrupted_reason = None
                return item is not None

            if sunrise is not None and sunrise <= now_local <= sunrise + timedelta(
                hours=_SUNRISE_RESUME_WINDOW_HOURS
            ):
                content = "Resumed the unfinished mowing task after sunrise"
                if names:
                    content += f" in {_zone_phrase(names)}"
                content += (
                    ". This is a continuation of the retained task; the native "
                    "mowing schedule does not gate this resume."
                )
                item = self._emit(
                    "NM1005",
                    "Unfinished mowing resumed after sunrise",
                    content,
                    kind="sunrise_resume",
                    confidence="inferred_from_retained_task_and_sunrise",
                    event_key=f"{self._task_token()}-{now_local.date().isoformat()}",
                )
                self._active_task["last_resume_gate"] = "sunrise"
                self._active_task["last_resumed_at"] = datetime.now(UTC).isoformat()
                self._interrupted_reason = None
                return item is not None
'''
replace_once(
    notifications_path,
    old_night_resume,
    new_night_resume,
    "night resume gate attribution",
)

helper_marker = '    def _next_schedule_start_today(\n'
helper = '''    def _scheduled_night_resume_gate(
        self, snapshot: dict[str, Any], now_local: datetime
    ) -> tuple[str, dict[str, Any]] | None:
        """Resolve the last gate for a retained native-schedule night pause."""
        period = self._schedule_period_containing_now(snapshot, now_local)
        if period is None:
            return None
        if (snapshot.get("settings") or {}).get("night_mow") is not False:
            return "schedule_window", period

        sunrise = self._sun_event_local(SUN_EVENT_SUNRISE, now_local.date())
        sunset = self._sun_event_local(SUN_EVENT_SUNSET, now_local.date())
        if sunrise is not None and now_local < sunrise:
            return None
        if sunset is not None and now_local >= sunset:
            return None

        start_dt = period["start_dt"]
        if sunrise is not None and start_dt < sunrise <= now_local:
            return "sunrise", period
        return "schedule_window", period

'''
replace_once(
    notifications_path,
    helper_marker,
    helper + helper_marker,
    "scheduled night resume gate helper",
)

old_night_pause = '''        if night_candidate is not None and (
            schedule_end is None or night_candidate < schedule_end - timedelta(minutes=5)
        ):
            names = list((self._active_task or {}).get("zone_names") or [])
            content = "The unfinished mowing task started returning to the charging station around sunset because Night mowing is disabled"
            if names:
                content += f" while mowing {_zone_phrase(names)}"
            if progress is not None:
                content += f" at {progress:g}% progress"
            content += ". It may resume after sunrise when the mower retains the task and charging allows."
            item = self._emit(
                "NM1004",
                "Mowing paused for night",
                content,
                kind="night_pause",
                confidence="inferred_from_sunset_and_night_mowing_off",
                event_key=f"{self._task_token()}-{now_local.date().isoformat()}",
            )
            self._interrupted_reason = "night"
            if self._active_task is not None:
                self._active_task["night_paused_at"] = datetime.now(UTC).isoformat()
            return item is not None
'''
new_night_pause = '''        if night_candidate is not None and (
            schedule_end is None or night_candidate < schedule_end - timedelta(minutes=5)
        ):
            names = list((self._active_task or {}).get("zone_names") or [])
            scheduled = (self._active_task or {}).get("origin") == "schedule"
            content = (
                "The unfinished scheduled mowing task started returning to the charging "
                "station around sunset because Night mowing is disabled"
                if scheduled
                else "The unfinished mowing task started returning to the charging station "
                "around sunset because Night mowing is disabled"
            )
            if names:
                content += f" while mowing {_zone_phrase(names)}"
            if progress is not None:
                content += f" at {progress:g}% progress"
            if scheduled:
                content += (
                    ". It can resume when a native mowing window is open and daylight "
                    "is available; whichever condition becomes available later is the "
                    "effective resume gate."
                )
            else:
                content += (
                    ". It may resume after sunrise when the mower retains the task and "
                    "charging allows. The native mowing schedule does not gate this "
                    "retained task."
                )
            item = self._emit(
                "NM1004",
                "Mowing paused for night",
                content,
                kind="night_pause",
                confidence="inferred_from_sunset_and_night_mowing_off",
                event_key=f"{self._task_token()}-{now_local.date().isoformat()}",
            )
            self._interrupted_reason = "night"
            if self._active_task is not None:
                self._active_task["night_paused_at"] = datetime.now(UTC).isoformat()
                self._active_task["night_pause_context"] = (
                    "native_schedule" if scheduled else "retained_task"
                )
            return item is not None
'''
replace_once(
    notifications_path,
    old_night_pause,
    new_night_pause,
    "night pause schedule-aware wording",
)

beta14_test = ROOT / "tests" / "test_v043_beta14.py"
replace_once(
    beta14_test,
    '''def test_beta14_identity():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta14"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta14.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta14")
''',
    '''def test_beta14_identity():
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta14.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta14")
''',
    "beta14 historical identity test",
)

write(ROOT / "tests" / "test_v043_beta15.py", r'''
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta15_identity():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta15"
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
    assert "the native mowing schedule does not gate this resume" in source
    assert "The native mowing schedule does not gate this retained task" in source


def test_night_pause_message_explains_native_schedule_gate():
    source = (COMPONENT / "notification_center.py").read_text(encoding="utf-8")
    assert "It can resume when a native mowing window is open and daylight" in source
    assert "effective resume gate" in source
    assert '"native_schedule" if scheduled else "retained_task"' in source
''')

notes = '''
title: Navimower 0.4.3-beta15

Notification attribution beta for night-paused mowing resumes.

### Changed
- Native scheduled mowing paused by sunset now treats schedule availability and daylight as separate resume gates. If sunrise comes first, the notification attributes the later schedule-window opening; if the window is already open before sunrise, it attributes daylight/sunrise.
- Night-pause notifications for native scheduled tasks now explain that both a native mowing window and daylight must allow the continuation.
- Retained non-scheduled / one-time mowing keeps its existing sunrise continuation semantics and explicitly does not depend on the native mowing schedule.
- Record the resolved night resume gate in retained task diagnostics so field reports can show whether `schedule_window` or `sunrise` allowed the continuation.

### Unchanged
- Navimower Schedule orchestration from beta13 is unchanged.
- Maintenance and Mowing Reports discovery remains paused.
- Clear and resume / Reboot Mower discovery remains unchanged and read-only.
'''
write(ROOT / ".github" / "release-notes" / "0.4.3-beta15.md", notes)

changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
marker = "# Changelog\n"
if not changelog.startswith(marker):
    raise SystemExit("changelog heading missing")
entry = '''

## 0.4.3-beta15

Schedule-aware night-pause and resume notification attribution.

### Changed

- Distinguish the two gates for native scheduled night resumes: an active schedule window and daylight.
- Attribute a retained scheduled resume to the schedule window when sunrise was already past, or to sunrise when the schedule window opened before daylight.
- Keep retained non-scheduled/one-time mowing independent from the native mowing schedule after a night pause.
- Preserve the resolved resume gate in task diagnostics.
'''
changelog_path.write_text(
    marker + dedent(entry) + changelog[len(marker):],
    encoding="utf-8",
)

# Builder-level smoke checks before the full repository suite.
source = notifications_path.read_text(encoding="utf-8")
assert "def _scheduled_night_resume_gate" in source
assert 'return "sunrise", period' in source
assert 'return "schedule_window", period' in source
assert 'kind="scheduled_night_resume"' in source
assert "the native mowing schedule does not gate this resume" in source
