from __future__ import annotations

import json
import re
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


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    if text.count(start) != 1 or text.count(end) != 1:
        raise SystemExit(
            f"{label}: expected unique boundaries, got start={text.count(start)} end={text.count(end)}"
        )
    start_at = text.index(start)
    end_at = text.index(end, start_at + len(start))
    return text[:start_at] + dedent(replacement).lstrip() + text[end_at:]


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).lstrip(), encoding="utf-8")


# ---------------------------------------------------------------- identity
manifest_path = COMPONENT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != "0.4.3-beta23":
    raise SystemExit(f"Expected beta23 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta24"
manifest_path.write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)


# ---------------------------------------------------------- schedule entities
schedule_path = COMPONENT / "navimower_schedule.py"
replace_once(
    schedule_path,
    '''    @property\n    def selected_zone_ids(self) -> tuple[int, ...]:\n        return tuple(sorted(self._selected_zone_ids))\n\n    @property\n    def start_time(self) -> time:\n''',
    '''    @property\n    def selected_zone_ids(self) -> tuple[int, ...]:\n        return tuple(sorted(self._selected_zone_ids))\n\n    @property\n    def configured(self) -> bool:\n        """Return whether the user has saved Navimower Schedule setup."""\n        return self._selection_configured\n\n    @property\n    def start_time(self) -> time:\n''',
    "public schedule configured marker",
)

replace_once(
    schedule_path,
    '''    def _pending_age(self) -> float | None:\n        pending = self._runtime.get("pending_command")\n        return _age_seconds(pending.get("sent_at")) if isinstance(pending, dict) else None\n\n    async def _reconcile_unconfirmed_mow_start(self, activity: Any) -> None:\n''',
    '''    def _pending_age(self) -> float | None:\n        pending = self._runtime.get("pending_command")\n        return _age_seconds(pending.get("sent_at")) if isinstance(pending, dict) else None\n\n    def _sync_active_cycle_id(self) -> bool:\n        """Attach the newly-created history cycle once cutting actually starts."""\n        if (\n            self._runtime.get("active_zone_id") is None\n            or self._runtime.get("active_cycle_id") is not None\n        ):\n            return False\n        history = getattr(self.coordinator, "history", None)\n        active = getattr(history, "active_session", None)\n        if not isinstance(active, dict) or not active.get("id"):\n            return False\n        try:\n            zone_id = int(self._runtime["active_zone_id"])\n        except (TypeError, ValueError):\n            return False\n        observed: set[int] = set()\n        for value in [\n            *(active.get("zone_ids") or []),\n            *(active.get("cycle_reset_zone_ids") or []),\n        ]:\n            try:\n                observed.add(int(value))\n            except (TypeError, ValueError):\n                continue\n        if zone_id not in observed:\n            return False\n        self._runtime["active_cycle_id"] = str(active["id"])\n        return True\n\n    async def _reconcile_unconfirmed_mow_start(self, activity: Any) -> None:\n''',
    "active cycle synchronization",
)

replace_once(
    schedule_path,
    '''        completed_now = await self._confirm_active_completion()\n        activity = data.get("activity")\n        await self._confirm_pending(activity)\n        await self._reconcile_unconfirmed_mow_start(activity)\n\n        if not in_window:\n''',
    '''        completed_now = await self._confirm_active_completion()\n        activity = data.get("activity")\n        await self._confirm_pending(activity)\n        if self._sync_active_cycle_id():\n            await self._save()\n        await self._reconcile_unconfirmed_mow_start(activity)\n\n        if not in_window:\n''',
    "active cycle synchronization hook",
)

replace_once(
    schedule_path,
    '''        if reset:\n            self.coordinator.start_new_mowing_cycle([zone_id], source=source)\n            post_reset_row = self._zone(zone_id) or row\n            self._runtime["active_zone_id"] = zone_id\n            self._runtime["active_cycle_id"] = post_reset_row.get("cycle_id")\n            self._runtime["active_zone_baseline_completed_at"] = later_iso(\n                row.get("last_completed_at"),\n                post_reset_row.get("last_completed_at"),\n            )\n''',
    '''        if reset:\n            self.coordinator.start_new_mowing_cycle([zone_id], source=source)\n            post_reset_row = self._zone(zone_id) or row\n            self._runtime["active_zone_id"] = zone_id\n            # start_new_mowing_cycle closes the old history session and arms a\n            # new one. Do not copy the previous zone model's cycle_id here; the\n            # real new session is attached by _sync_active_cycle_id after the\n            # mower confirms cutting.\n            self._runtime["active_cycle_id"] = None\n            self._runtime["active_zone_baseline_completed_at"] = later_iso(\n                row.get("last_completed_at"),\n                post_reset_row.get("last_completed_at"),\n            )\n''',
    "avoid stale scheduler cycle id",
)


# Hide schedule entities until the user has saved schedule setup. Also remove
# stale registry rows created by earlier betas on entries that were never set up.
switch_path = COMPONENT / "switch.py"
replace_once(
    switch_path,
    '''    entities = [NavimowSwitch(coordinator, desc) for desc in supported_descriptions]\n    if getattr(coordinator, "navimower_schedule", None) is not None:\n        entities.append(NavimowerScheduleSwitch(coordinator))\n    async_add_entities(entities)\n''',
    '''    entities = [NavimowSwitch(coordinator, desc) for desc in supported_descriptions]\n    controller = getattr(coordinator, "navimower_schedule", None)\n    if controller is not None and controller.configured:\n        entities.append(NavimowerScheduleSwitch(coordinator))\n    else:\n        registry = er.async_get(hass)\n        entity_id = registry.async_get_entity_id(\n            "switch", DOMAIN, f"{coordinator.sn}_navimower_schedule"\n        )\n        if entity_id is not None:\n            registry.async_remove(entity_id)\n    async_add_entities(entities)\n''',
    "schedule switch setup gate",
)

time_path = COMPONENT / "time.py"
replace_once(
    time_path,
    '''from homeassistant.core import HomeAssistant\nfrom homeassistant.helpers.entity_platform import AddEntitiesCallback\n''',
    '''from homeassistant.core import HomeAssistant\nfrom homeassistant.helpers import entity_registry as er\nfrom homeassistant.helpers.entity_platform import AddEntitiesCallback\n''',
    "time entity registry import",
)
replace_once(
    time_path,
    '''    controller = getattr(coordinator, "navimower_schedule", None)\n    if controller is None:\n        return\n    async_add_entities(\n''',
    '''    controller = getattr(coordinator, "navimower_schedule", None)\n    if controller is None or not controller.configured:\n        registry = er.async_get(hass)\n        for key in ("start", "end"):\n            entity_id = registry.async_get_entity_id(\n                "time", DOMAIN, f"{coordinator.sn}_navimower_schedule_{key}"\n            )\n            if entity_id is not None:\n                registry.async_remove(entity_id)\n        return\n    async_add_entities(\n''',
    "schedule time setup gate",
)


# ----------------------------------------------------------- notifications
notification_path = COMPONENT / "notification_center.py"
replace_once(
    notification_path,
    '''        ids = self._observed_task_zone_ids(snapshot)\n        names_by_id = _zone_names(snapshot)\n        names = [names_by_id.get(value, f"Zone {value}") for value in ids]\n        content = "Started an external mowing task"\n        if names:\n            content += f" in {_zone_phrase(names)}"\n        else:\n            content += " for all zones or a target list not exposed by the mower"\n        content += "."\n        item = self._emit(\n            "NM1003",\n            "External mowing task started",\n            content,\n            kind="external_mowing_started",\n            confidence="observed_external_start",\n        )\n        self._active_task = {\n            "task_id": (item or {}).get("message_id"),\n            "origin": "external",\n            "trigger": "external_or_vendor",\n            "zone_ids": ids,\n            "zone_names": names,\n            "ordered": None,\n            "started_at": datetime.now(UTC).isoformat(),\n            "observed_mow_start_type": self._mqtt_value("mow_start_type"),\n        }\n        self._interrupted_reason = None\n        return item is not None\n''',
    '''        ids = self._observed_task_zone_ids(snapshot)\n        names_by_id = _zone_names(snapshot)\n        names = [names_by_id.get(value, f"Zone {value}") for value in ids]\n        # This Home Assistant instance cannot prove the source when no fresh\n        # local Mow/Resume trace exists. NM1003 used to be titled\n        # "External mowing task started", but that was too strong: the command\n        # may have come from another Navimower HA instance, the app, mower\n        # controls or voice control. The observed fallback does not assume it\n        # came from the mobile app or from any other specific external source.\n        content = "Observed a mowing task start"\n        if names:\n            content += f" in {_zone_phrase(names)}"\n        else:\n            content += " for all zones or a target list not exposed by the mower"\n        content += "."\n        item = self._emit(\n            "NM1003",\n            "Mowing task started",\n            content,\n            kind="observed_mowing_started",\n            confidence="observed_start_without_local_command",\n        )\n        self._active_task = {\n            "task_id": (item or {}).get("message_id"),\n            "origin": "observed",\n            "trigger": "observed_without_local_command",\n            "zone_ids": ids,\n            "zone_names": names,\n            "ordered": None,\n            "started_at": datetime.now(UTC).isoformat(),\n            "observed_mow_start_type": self._mqtt_value("mow_start_type"),\n        }\n        self._interrupted_reason = None\n        return item is not None\n''',
    "neutral observed mowing start",
)

replace_once(
    notification_path,
    '''        threshold = _as_float((snapshot.get("settings") or {}).get("return_battery_level"))\n        if battery is not None and threshold is not None and battery <= threshold + 2:\n            names = list((self._active_task or {}).get("zone_names") or [])\n            content = f"The unfinished mowing task returned to charge at {battery:g}% battery"\n            if names:\n                content += f" while mowing {_zone_phrase(names)}"\n            if progress is not None:\n                content += f" at {progress:g}% task progress"\n            content += "."\n            item = self._emit(\n                "NM1007",\n                "Mowing paused for charging",\n                content,\n                kind="charging_pause",\n                confidence="inferred_from_return_battery_threshold",\n            )\n            self._interrupted_reason = "charging"\n            if self._active_task is not None:\n                self._active_task["charging_paused_at"] = datetime.now(UTC).isoformat()\n            return item is not None\n''',
    '''        threshold = _as_float((snapshot.get("settings") or {}).get("return_battery_level"))\n        if battery is not None and threshold is not None and battery <= threshold + 2:\n            # The vendor Device feed already reports the low-battery return,\n            # normally as code 1502. Keep that vendor row as the single visible\n            # charging-pause notification so schedule and non-schedule users see\n            # the same event without a duplicate Navimower-local NM1007 row.\n            # NM1007 "Mowing paused for charging" is therefore retained only as\n            # historical stored data from older releases. Internal task context\n            # is still kept so NM1008 can explain the later charging resume.\n            self._interrupted_reason = "charging"\n            if self._active_task is not None:\n                self._active_task["charging_paused_at"] = datetime.now(UTC).isoformat()\n                self._active_task["charging_pause_confidence"] = (\n                    "inferred_from_return_battery_threshold"\n                )\n            return False\n''',
    "charging notification deduplication",
)


# --------------------------------------------------------------- README
readme_path = ROOT / "README.md"
readme = readme_path.read_text(encoding="utf-8")

readme = replace_between(
    readme,
    "### Latest notification\n",
    "### Settings and controls\n",
    r'''
### Latest notification

Navimower exposes one merged **Latest notification** timeline. It combines the
Navimow app's Device feed with Home Assistant-side context that Navimower can
prove from confirmed mower state and fresh local command traces.

- up to **10 vendor** Device notifications are retained with `origin: vendor`;
- up to **20 Navimower** context rows are retained with `origin: navimower`;
- vendor rows keep their original title, content, timestamp, read state and code;
- Navimower rows explain confirmed Home Assistant starts, Resume/Dock actions,
  night interruptions and retained-task resumes without inventing vendor codes;
- a mowing start with no fresh command trace in this Home Assistant instance is
  shown neutrally as **Mowing task started** / observed start. It is not labelled
  as an app or "external" command because another HA instance, the mower itself
  or another control path may have issued it;
- low-battery return uses the vendor Device notification as the single visible
  charging-pause row. Navimower still retains the unfinished task context and can
  add **Mowing resumed after charging** when cutting really resumes;
- local activity rows are created only after confirmed vendor activity, not from
  an optimistic button state.

`navimower.mark_notification_read` marks the selected row using the correct
origin-specific path: local rows stay local, while vendor rows use the vendor
message flow. `navimower.mark_all_notifications_read` handles both retained local
rows and the vendor Device feed.

Vendor notification read state remains account-specific. These actions operate
on the private-cloud Navimow account used by that config entry and do not mark
messages read in other shared accounts.

Notification history is user-facing event information and does not replace the
live Problem/Error state model.

''',
    "README notification section",
)

readme = replace_between(
    readme,
    "### i2 AWD experimental support\n",
    "### Entity reference and model support\n",
    r'''
### i2 AWD experimental support

Initial i2 AWD support is capability-driven and may expose settings such as Eco
mode, Narrow zone adapt, Advanced slope mode, Grass pattern enhancement,
Progress retention, Mowing cycle interval, Headlight, Night animal protection,
Terrain adapt, Edge sense, TCS, positioning controls and Global cutting height
when the corresponding vendor fields are present.

> [!CAUTION]
> **i2 AWD controls remain experimental.** Diagnostics have provided useful
> capability evidence, but not every control/write path has been field-validated
> across i2 AWD models and firmware. Availability and write semantics can vary.

Default battery-setting ranges are 5–20% for return-to-dock and 70–100% for the
charging limit unless the mower reports its own supported min/max limits.

''',
    "README i2 current-state section",
)

readme = replace_between(
    readme,
    "### Built-in Map Camera removal\n",
    "## Options\n",
    r'''
### Legacy Map Camera

The old built-in SVG **Legacy Map Camera** is removed. Existing dashboards should
use Navimower Map Card instead. This does not affect camera/VisionFence-related
mower settings such as Camera positioning (EFLS); only the old Home Assistant SVG
map-camera entity was removed.

''',
    "README legacy map camera section",
)

readme = replace_between(
    readme,
    "## Home Assistant diagnostics\n",
    "## 0.4.2 beta development\n",
    r'''
## Home Assistant diagnostics

Use Home Assistant's normal **Download diagnostics** action from the Navimower
integration/config-entry menu.

The generated document is snapshot-only and sanitized. It contains general
mower, connectivity, positioning, map, telemetry, history, capability,
maintenance, Navimower Schedule, Problem/Error and notification-center context.
Tokens, password, email, UID, full mower serial, GPS coordinates and other
sensitive account/network identifiers are redacted.

Downloading diagnostics does not execute mower commands or notification read
mutations. Older development-only passive discovery/export controls are not part
of the normal integration UI.

''',
    "README diagnostics current-state section",
)

readme = replace_between(
    readme,
    "## Current limitations\n",
    "## Credits and licence\n",
    r'''
## Current limitations

- Private-cloud behavior is undocumented and may change without notice.
- Model and firmware fields differ; unsupported settings are not invented.
- i2 AWD control support remains experimental and is not equally field-tested
  across models/firmware.
- Exact dense route history depends on live MQTT pose samples; missing samples
  are not reconstructed.
- Map writes, boundary edits and other destructive map editing are deliberately
  not included.

For release-by-release changes, see [CHANGELOG.md](CHANGELOG.md).

''',
    "README current limitations",
)

readme = readme.replace(
    "All three were field-tested bidirectionally on H215 during the\n0.4.1 beta cycle.",
    "All three have been field-tested bidirectionally on H215.",
)

schedule_setup_marker = (
    "Only the explicitly selected zones are enrolled. Adding another zone to the map\n"
    "does not automatically add it to the scheduler.\n"
)
if readme.count(schedule_setup_marker) != 1:
    raise SystemExit("README schedule setup marker missing")
readme = readme.replace(
    schedule_setup_marker,
    schedule_setup_marker
    + "\nBefore this setup is saved, Navimower Schedule switch/time entities are intentionally\n"
      "not created on the mower device. Saving the setup reloads the config entry and\n"
      "exposes the controls for that mower. Turning the Schedule switch off later does\n"
      "not remove the configured controls.\n",
    1,
)

# README is current-state documentation: move installation/setup before feature
# details and omit embedded release-history sections entirely.
heading_re = re.compile(r"^## .+$", re.MULTILINE)
matches = list(heading_re.finditer(readme))
if not matches:
    raise SystemExit("README has no top-level sections")
preamble = readme[: matches[0].start()].rstrip() + "\n\n"
sections: dict[str, str] = {}
for index, match in enumerate(matches):
    end = matches[index + 1].start() if index + 1 < len(matches) else len(readme)
    heading = match.group(0)[3:].strip()
    if heading in sections:
        raise SystemExit(f"Duplicate README section {heading!r}")
    sections[heading] = readme[match.start():end].strip() + "\n\n"

required = {
    "Installation",
    "Recommended account arrangement",
    "Setup flow",
    "Main features",
    "Navimower Schedule",
    "Connectivity and polling",
    "Navimower Map Card",
    "Options",
    "Home Assistant diagnostics",
    "Current limitations",
    "Project origins",
    "Credits and licence",
}
missing = required - set(sections)
if missing:
    raise SystemExit(f"README missing sections: {sorted(missing)}")

order = [
    "Installation",
    "Recommended account arrangement",
    "Setup flow",
    "Main features",
    "Navimower Schedule",
    "Connectivity and polling",
    "Navimower Map Card",
    "Options",
    "Home Assistant diagnostics",
    "Current limitations",
    "Project origins",
    "Credits and licence",
]
readme = preamble + "".join(sections[name] for name in order)
if "## 0.4.2 beta development" in readme or "## Upgrade from 0.4.0 to 0.4.1" in readme:
    raise SystemExit("README still contains embedded release-history sections")
readme_path.write_text(readme.rstrip() + "\n", encoding="utf-8")


# ---------------------------------------------- historical beta23 test identity
beta23_test = ROOT / "tests" / "test_v043_beta23.py"
replace_once(
    beta23_test,
    '''def test_beta23_identity_and_notes():\n    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))\n    assert manifest["version"] == "0.4.3-beta23"\n    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta23.md").read_text(encoding="utf-8")\n    assert notes.startswith("title: Navimower 0.4.3-beta23")\n''',
    '''def test_beta23_release_notes_remain_available():\n    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta23.md").read_text(encoding="utf-8")\n    assert notes.startswith("title: Navimower 0.4.3-beta23")\n''',
    "beta23 historical identity",
)
replace_once(beta23_test, "import json\n", "", "remove unused beta23 json import")


# -------------------------------------------------------------- beta24 tests
write(ROOT / "tests" / "test_v043_beta24.py", r'''
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _source(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


def test_beta24_identity_and_release_notes():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta24"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta24.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta24")


def test_schedule_entities_require_saved_setup_and_cleanup_old_registry_rows():
    schedule = _source("navimower_schedule.py")
    switch = _source("switch.py")
    time_source = _source("time.py")
    for source in (schedule, switch, time_source):
        ast.parse(source)

    assert "def configured(self) -> bool:" in schedule
    assert "return self._selection_configured" in schedule
    assert "controller is not None and controller.configured" in switch
    assert 'f"{coordinator.sn}_navimower_schedule"' in switch
    assert "registry.async_remove(entity_id)" in switch
    assert "controller is None or not controller.configured" in time_source
    assert 'f"{coordinator.sn}_navimower_schedule_{key}"' in time_source


def test_scheduler_cycle_id_waits_for_the_real_new_history_session():
    source = _source("navimower_schedule.py")
    ast.parse(source)
    assert "def _sync_active_cycle_id(self) -> bool:" in source
    assert 'active.get("cycle_reset_zone_ids")' in source
    assert 'self._runtime["active_cycle_id"] = str(active["id"])' in source
    assert "if self._sync_active_cycle_id():" in source

    send_start = source.index("    async def _async_send_mow")
    send_end = source.index("    async def _async_send_dock", send_start)
    send = source[send_start:send_end]
    assert 'self._runtime["active_cycle_id"] = None' in send
    assert 'post_reset_row.get("cycle_id")' not in send


def test_observed_mowing_start_is_neutral_when_this_ha_has_no_command_trace():
    source = _source("notification_center.py")
    ast.parse(source)
    start = source.index("        ids = self._observed_task_zone_ids(snapshot)")
    end = source.index("    def _handle_mowing_stop", start)
    fallback = source[start:end]
    assert '"NM1003"' in fallback
    assert '"Mowing task started"' in fallback
    assert 'kind="observed_mowing_started"' in fallback
    assert 'confidence="observed_start_without_local_command"' in fallback
    assert '"origin": "observed"' in fallback
    assert '"trigger": "observed_without_local_command"' in fallback
    assert '_emit(\n            "NM1003",\n            "External mowing task started"' not in fallback


def test_low_battery_pause_uses_vendor_row_but_retains_resume_context():
    source = _source("notification_center.py")
    ast.parse(source)
    start = source.index('        threshold = _as_float((snapshot.get("settings") or {}).get("return_battery_level"))')
    end = source.index('        self._interrupted_reason = "unknown"', start)
    charging = source[start:end]
    assert 'self._interrupted_reason = "charging"' in charging
    assert '"charging_paused_at"' in charging
    assert '"inferred_from_return_battery_threshold"' in charging
    assert 'return False' in charging
    assert '_emit(' not in charging
    assert '"NM1008"' in source
    assert '"Mowing resumed after charging"' in source


def test_readme_is_current_state_documentation_with_installation_before_features():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.index("## Installation") < readme.index("## Main features")
    assert readme.index("## Setup flow") < readme.index("## Main features")
    assert "## Navimower Schedule" in readme
    assert "Before this setup is saved" in readme
    assert "## 0.4.2 beta development" not in readme
    assert "## Upgrade from 0.4.0 to 0.4.1" not in readme
    assert "### v0.3.4" not in readme
    assert "External mowing task started" not in readme
    assert "low-battery return uses the vendor Device notification" in readme
    assert "[CHANGELOG.md](CHANGELOG.md)" in readme
''')


# ---------------------------------------------------------- changelog / notes
changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
marker = "# Changelog\n\n"
if not changelog.startswith(marker):
    raise SystemExit("Unexpected changelog header")
section = '''## 0.4.3-beta24\n\nRelease-candidate cleanup for Navimower Schedule, notifications and documentation.\n\n### Changed\n\n- Expose Navimower Schedule switch/time entities only after the user has saved Schedule setup for that mower; remove stale pre-setup registry entities created by earlier betas.\n- Rename un-attributed mowing starts to the neutral `Mowing task started` wording instead of claiming an `External` source when this Home Assistant instance has no fresh local command trace.\n- Use the vendor Device-feed low-battery return as the single visible charging-pause notification while retaining local task context for `Mowing resumed after charging`.\n- Reorder README into installation/setup first and current functionality second; remove embedded beta/upgrade history and keep release history in `CHANGELOG.md`.\n\n### Fixed\n\n- Stop copying a stale pre-reset zone `cycle_id` into Navimower Schedule runtime. A new dispatch now leaves `active_cycle_id` empty until the newly-created history session for that active zone is observed, then synchronizes the real session ID.\n\n### Unchanged\n\n- Navimower Schedule completion remains based on fresh `Last completed` advancement and is not changed by the cycle-ID diagnostics fix.\n- Charging/rain/night behavior remains mower-owned in 24-hour mode; charging resume attribution remains available without duplicating the vendor low-battery notification.\n\n\n'''
changelog_path.write_text(marker + section + changelog[len(marker):], encoding="utf-8")

write(ROOT / ".github" / "release-notes" / "0.4.3-beta24.md", r'''
title: Navimower 0.4.3-beta24

Release-candidate cleanup for Navimower Schedule, notifications and README structure.

### Navimower Schedule UI
- The **Navimower schedule** switch and its time entities are now created only after Schedule setup has been saved for that mower.
- Entries that never completed Schedule setup no longer show unused Schedule controls; stale registry rows created by earlier betas are removed on reload.
- A configured Schedule remains visible when its switch is turned off. Configuration and enabled state are intentionally separate.

### Notification timeline
- A mowing start that this Home Assistant instance cannot tie to a fresh local Mow/Resume command is now labelled neutrally as **Mowing task started** with observed-source context. It is no longer called **External mowing task started**, because the command may come from another Navimower HA instance, the mower/app or another control path.
- Low-battery return no longer creates a duplicate Navimower-local **Mowing paused for charging** row beside the vendor Device notification. The vendor row remains the user-facing charging-pause event for both Schedule and non-Schedule users.
- Navimower still retains the unfinished task/progress context and can emit **Mowing resumed after charging** when confirmed cutting resumes.

### Scheduler diagnostics fix
- A new `reset=true` Schedule dispatch no longer copies the old zone model's `cycle_id` into scheduler runtime.
- `active_cycle_id` is attached only after the actual new history session for the active zone exists. This corrects diagnostics/state context without changing `Last completed` or zone-completion arbitration.

### Documentation
- README now presents **Installation**, account arrangement and setup before feature details.
- Embedded beta development, upgrade summaries and old release-history blocks were removed from README; release history remains in **CHANGELOG.md**.
- Notification and diagnostics documentation now describes current behavior instead of old beta-specific behavior.

### Release-candidate status
- This beta is intended as the next release-candidate baseline. The ongoing 24-hour Navimower Schedule field run remains on beta22 until its first full round rolls into round 2, so that rollover can be validated without changing the test environment mid-run.
''')
