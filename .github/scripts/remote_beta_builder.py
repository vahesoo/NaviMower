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


def replace_section(path: Path, start: str, end: str, replacement: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(start) != 1 or text.count(end) != 1:
        raise SystemExit(f"{label}: section markers changed")
    a = text.index(start)
    b = text.index(end, a)
    path.write_text(text[:a] + dedent(replacement).lstrip("\n") + text[b:], encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).lstrip(), encoding="utf-8")


# Identity.
manifest_path = COMPONENT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != "0.4.3-beta17":
    raise SystemExit(f"Expected beta17 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta18"
manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# Vendor end_time/high percentage stays visible as diagnostics, not authoritative completion.
coordinator_path = COMPONENT / "coordinator.py"
replace_once(
    coordinator_path,
    '''                    "last_completed_at": (\n                        dt_util.utc_from_timestamp(end_time).isoformat()\n                        if end_time\n                        and (percentage or 0) >= VENDOR_COMPLETION_PROGRESS_MIN\n                        else None\n                    ),\n''',
    '''                    "vendor_start_time": start_time,\n                    "vendor_end_time": end_time,\n                    "vendor_completed_at": (\n                        dt_util.utc_from_timestamp(end_time).isoformat()\n                        if end_time\n                        and (percentage or 0) >= VENDOR_COMPLETION_PROGRESS_MIN\n                        else None\n                    ),\n                    "last_completed_at": None,\n''',
    "vendor completion candidate",
)
replace_section(
    coordinator_path,
    "    def _session_completed(self, snapshot: dict[str, Any]) -> bool | None:\n",
    "    def _active_cutting_height(self, snapshot: dict[str, Any]) -> int | None:\n",
    '''
    def _session_completed(self, snapshot: dict[str, Any]) -> bool | None:
        """Return success only for zones confirmed inside this observed cycle."""
        del snapshot
        active = self.history.active_session
        if not active:
            return None
        selected = {
            value for raw in active.get("zone_ids") or []
            if (value := _as_int(raw)) is not None
        }
        if not selected:
            return None
        confirmed = {
            value for raw in active.get("task_zone_completion_confirmed") or []
            if (value := _as_int(raw)) is not None
        }
        return selected.issubset(confirmed)

''',
    "session completion resolver",
)

history_path = COMPONENT / "history.py"
replace_once(
    history_path,
    "def _metadata(session: dict[str, Any]) -> dict[str, Any]:\n",
    '''def _iso_ms(value: Any) -> int | None:\n    if not value:\n        return None\n    try:\n        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))\n        if parsed.tzinfo is None:\n            parsed = parsed.replace(tzinfo=UTC)\n        return int(parsed.timestamp() * 1000)\n    except (TypeError, ValueError, OverflowError, OSError):\n        return None\n\n\ndef _metadata(session: dict[str, Any]) -> dict[str, Any]:\n''',
    "ISO timestamp parser",
)
replace_once(
    history_path,
    '''    merged["cycle_reset_zone_ids"] = _unique_ints(\n        previous.get("cycle_reset_zone_ids"),\n        continuation.get("cycle_reset_zone_ids"),\n    )\n    merged["zone_cycle_boundaries"] = sorted(\n''',
    '''    merged["cycle_reset_zone_ids"] = _unique_ints(\n        previous.get("cycle_reset_zone_ids"),\n        continuation.get("cycle_reset_zone_ids"),\n    )\n    task_progress = dict(previous.get("task_zone_progress") or {})\n    task_progress.update(continuation.get("task_zone_progress") or {})\n    merged["task_zone_progress"] = task_progress\n    merged["task_zone_seen_incomplete"] = _unique_ints(\n        previous.get("task_zone_seen_incomplete"), continuation.get("task_zone_seen_incomplete")\n    )\n    merged["task_zone_completion_confirmed"] = _unique_ints(\n        previous.get("task_zone_completion_confirmed"), continuation.get("task_zone_completion_confirmed")\n    )\n    merged["zone_cycle_boundaries"] = sorted(\n''',
    "merge completion evidence",
)
replace_once(
    history_path,
    "    async def _async_migrate_legacy_store(self) -> None:\n",
    '''    async def _async_repair_unverified_zone_completions(self) -> None:\n        """Remove beta-era raw-vendor/reset-boundary completion timestamps."""\n        repaired = 0\n        with self._lock:\n            reset_boundaries: dict[int, list[int]] = {}\n            for session in self._cache.values():\n                for item in (session or {}).get("zone_cycle_boundaries") or []:\n                    if not isinstance(item, dict):\n                        continue\n                    zone_id, at_ms = _as_int(item.get("zone_id")), _as_int(item.get("at_ms"))\n                    if zone_id is not None and at_ms is not None:\n                        reset_boundaries.setdefault(zone_id, []).append(at_ms)\n            for key, original in list(self._zone_history.items()):\n                record = dict(original)\n                completed_ms = _iso_ms(record.get("last_completed_at"))\n                if completed_ms is None:\n                    continue\n                zone_id = _as_int(record.get("id")) or _as_int(key)\n                unverified = _as_int(record.get("last_completed_progress")) is None\n                reset_stamp = bool(\n                    zone_id is not None and any(\n                        abs(completed_ms - boundary) <= 60_000\n                        for boundary in reset_boundaries.get(zone_id, [])\n                    )\n                )\n                if not (unverified or reset_stamp):\n                    continue\n                record.pop("last_completed_at", None)\n                record.pop("last_completed_progress", None)\n                self._zone_history[str(key)] = record\n                repaired += 1\n        if repaired:\n            await self._index_store.async_save(self._index_data())\n            _LOGGER.info("Removed %d unverified Navimower zone completion timestamp(s)", repaired)\n\n    async def _async_migrate_legacy_store(self) -> None:\n''',
    "completion repair helper",
)
replace_once(
    history_path,
    '''        await self._async_merge_adjacent_sessions()\n        await self._async_remove_empty_completed_sessions()\n        await self._async_migrate_legacy_store()\n''',
    '''        await self._async_merge_adjacent_sessions()\n        await self._async_remove_empty_completed_sessions()\n        await self._async_repair_unverified_zone_completions()\n        await self._async_migrate_legacy_store()\n''',
    "completion repair load hook",
)
replace_once(
    history_path,
    '''            "task_zone_progress": {str(value): 0 for value in zone_ids},\n            "segment_starts_ms": [start_ms],\n''',
    '''            "task_zone_progress": {str(value): 0 for value in zone_ids},\n            "task_zone_seen_incomplete": [],\n            "task_zone_completion_confirmed": [],\n            "segment_starts_ms": [start_ms],\n''',
    "new session completion evidence",
)
replace_once(
    history_path,
    '''        previous.setdefault("visited_zone_ids", [])\n        previous.setdefault("task_zone_progress", {})\n        previous["zone_ids"] = _unique_ints(\n''',
    '''        previous.setdefault("visited_zone_ids", [])\n        previous.setdefault("task_zone_progress", {})\n        previous.setdefault("task_zone_seen_incomplete", [])\n        previous.setdefault("task_zone_completion_confirmed", [])\n        previous["zone_ids"] = _unique_ints(\n''',
    "resume completion evidence",
)
replace_once(
    history_path,
    '''            if completed is not None:\n                active["completed"] = completed\n\n            # Once a mowing session exists, preserve transit and pause positions.\n''',
    '''            if completed is not None:\n                if completed is True:\n                    selected = set(_unique_ints(active.get("zone_ids")))\n                    confirmed = set(_unique_ints(active.get("task_zone_completion_confirmed")))\n                    completed = bool(selected) and selected.issubset(confirmed)\n                active["completed"] = completed\n\n            # Once a mowing session exists, preserve transit and pause positions.\n''',
    "process completion guard",
)

# Explicit reset: keep diagnostics/session boundary, never manufacture a completion timestamp.
replace_once(
    history_path,
    '''            final_progress: dict[str, int] = {}\n            known_progress: list[int] = []\n            for zone_id in relevant:\n                state = self._zone_progress_state.get(str(zone_id)) or {}\n                progress = _as_int(state.get("peak_progress"))\n                if progress is None:\n                    progress = _as_int(state.get("progress"))\n                if progress is not None:\n                    final_progress[str(zone_id)] = progress\n                    known_progress.append(progress)\n            completed = bool(known_progress) and all(\n                value >= VENDOR_COMPLETION_PROGRESS_MIN for value in known_progress\n            )\n''',
    '''            final_progress: dict[str, int] = {}\n            for zone_id in relevant:\n                state = self._zone_progress_state.get(str(zone_id)) or {}\n                progress = _as_int(state.get("peak_progress"))\n                if progress is None:\n                    progress = _as_int(state.get("progress"))\n                if progress is not None:\n                    final_progress[str(zone_id)] = progress\n            confirmed = set(_unique_ints((active or {}).get("task_zone_completion_confirmed")))\n            completed = bool(relevant) and set(relevant).issubset(confirmed)\n''',
    "explicit reset completion evidence",
)
replace_once(
    history_path,
    '''            if completed:\n                for zone_id in relevant:\n                    progress = final_progress.get(str(zone_id))\n                    record = dict(self._zone_history.get(str(zone_id)) or {})\n                    record.update({\n                        "id": zone_id,\n                        "name": record.get("name") or f"Zone {zone_id}",\n                        "last_completed_at": _iso(boundary_ms),\n                        "last_completed_progress": progress,\n                    })\n                    self._zone_history[str(zone_id)] = record\n''',
    "",
    "remove explicit reset completion timestamp",
)

# Vendor reset: boundary only. Completion evidence is cleared for the new cycle.
replace_once(
    history_path,
    '''                boundaries = active.setdefault("zone_cycle_boundaries", [])\n                for _previous, row, previous_peak in reset_rows:\n''',
    '''                boundaries = active.setdefault("zone_cycle_boundaries", [])\n                confirmed_before = set(_unique_ints(active.get("task_zone_completion_confirmed")))\n                for _previous, row, previous_peak in reset_rows:\n''',
    "vendor reset prior evidence",
)
replace_once(
    history_path,
    '''                    completed_zone = previous_peak >= VENDOR_COMPLETION_PROGRESS_MIN\n                    completed_flags.append(completed_zone)\n                    if completed_zone:\n                        record = dict(self._zone_history.get(str(zone_id)) or {})\n                        record.update({"id": zone_id, "name": row.get("name") or record.get("name") or f"Zone {zone_id}", "last_completed_at": _iso(at_ms), "last_completed_progress": previous_peak})\n                        self._zone_history[str(zone_id)] = record\n                self._update_active_metadata_locked(active)\n''',
    '''                    completed_zone = zone_id in confirmed_before\n                    completed_flags.append(completed_zone)\n                reset_set = set(reset_zone_ids)\n                active["task_zone_seen_incomplete"] = [\n                    value for value in _unique_ints(active.get("task_zone_seen_incomplete"))\n                    if value not in reset_set\n                ]\n                active["task_zone_completion_confirmed"] = [\n                    value for value in _unique_ints(active.get("task_zone_completion_confirmed"))\n                    if value not in reset_set\n                ]\n                task_progress = active.setdefault("task_zone_progress", {})\n                for zone_id in reset_set:\n                    task_progress[str(zone_id)] = 0\n                if active.get("completion_reason") == "vendor_progress":\n                    active["completed"] = None\n                    active["completion_reason"] = None\n                    active["final_progress"] = {}\n                self._update_active_metadata_locked(active)\n''',
    "vendor reset is not completion",
)

# Never import raw vendor last_completed; collect current-cycle low->high evidence.
replace_once(
    history_path,
    '''                for key in (\n                    "last_started_at",\n                    "last_mowed_at",\n                    "last_completed_at",\n                ):\n''',
    '''                for key in (\n                    "last_started_at",\n                    "last_mowed_at",\n                ):\n''',
    "ignore raw completion timestamp",
)
replace_once(
    history_path,
    '''                task_progress = active.setdefault("task_zone_progress", {})\n                for zone_id in active.get("zone_ids") or []:\n                    task_progress.setdefault(str(zone_id), 0)\n                active_zone_id = _as_int(active_progress_zone_id)\n''',
    '''                task_progress = active.setdefault("task_zone_progress", {})\n                for zone_id in active.get("zone_ids") or []:\n                    task_progress.setdefault(str(zone_id), 0)\n                seen_incomplete = set(_unique_ints(active.get("task_zone_seen_incomplete")))\n                completion_confirmed = set(_unique_ints(active.get("task_zone_completion_confirmed")))\n                active_zone_id = _as_int(active_progress_zone_id)\n''',
    "completion evidence sets",
)
replace_once(
    history_path,
    '''                        if progress is not None:\n                            previous_progress = _as_int(\n                                task_progress.get(str(active_zone_id))\n                            )\n''',
    '''                        if progress is not None:\n                            if progress < VENDOR_COMPLETION_PROGRESS_MIN:\n                                seen_incomplete.add(active_zone_id)\n                                completion_confirmed.discard(active_zone_id)\n                            elif active_zone_id in seen_incomplete:\n                                completion_confirmed.add(active_zone_id)\n                            previous_progress = _as_int(\n                                task_progress.get(str(active_zone_id))\n                            )\n''',
    "observe cycle progress",
)
replace_once(
    history_path,
    '''                if active.get("cutting_height_mm") is None:\n''',
    '''                active["task_zone_seen_incomplete"] = sorted(seen_incomplete)\n                active["task_zone_completion_confirmed"] = sorted(completion_confirmed)\n                if active.get("cutting_height_mm") is None:\n''',
    "persist cycle evidence",
)
replace_once(
    history_path,
    '''                percentages = [\n                    _as_int(task_progress.get(str(zone_id)))\n                    for zone_id in active.get("zone_ids") or []\n                    if _as_int(task_progress.get(str(zone_id))) is not None\n                ]\n                if (\n                    percentages\n                    and len(percentages) == len(active.get("zone_ids") or [])\n                    and all(value >= VENDOR_COMPLETION_PROGRESS_MIN for value in percentages)\n                ):\n''',
    '''                selected_zone_ids = _unique_ints(active.get("zone_ids"))\n                if (\n                    selected_zone_ids\n                    and set(selected_zone_ids).issubset(completion_confirmed)\n                ):\n''',
    "confirmed completion rule",
)

# Existing regressions: reset is no longer completion and valid completion needs low->high evidence.
test_path = ROOT / "tests" / "test_history_merge.py"
replace_once(test_path,
    '''    zone_history = manager.zone_history()["24"]\n    assert zone_history["last_completed_progress"] == 98\n    assert zone_history["last_completed_at"]\n''',
    '''    zone_history = manager.zone_history().get("24", {})\n    assert "last_completed_progress" not in zone_history\n    assert "last_completed_at" not in zone_history\n''',
    "reset regression")
replace_once(test_path,
    '''    manager.update_zone_history(\n        {"zones": [{"id": 13, "pct": 97}]},\n        [{"id": 13, "name": "Street", "percentage": 97}],\n        active_zone_progress=97,\n        active_progress_zone_id=13,\n    )\n''',
    '''    manager.update_zone_history(\n        {"zones": [{"id": 13, "pct": 40}]},\n        [{"id": 13, "name": "Street", "percentage": 40}],\n        active_zone_progress=40,\n        active_progress_zone_id=13,\n    )\n    manager.update_zone_history(\n        {"zones": [{"id": 13, "pct": 97}]},\n        [{"id": 13, "name": "Street", "percentage": 97}],\n        active_zone_progress=97,\n        active_progress_zone_id=13,\n    )\n''',
    "threshold evidence regression")
replace_once(test_path,
    '''    manager.update_zone_history(\n        {"zones": [{"id": 24, "pct": 97}]},\n        [{"id": 24, "name": "Yard", "progress": 97, "percentage": 23}],\n        active_zone_progress=97,\n        active_progress_zone_id=24,\n    )\n''',
    '''    manager.update_zone_history(\n        {"zones": [{"id": 24, "pct": 23}]},\n        [{"id": 24, "name": "Yard", "progress": 23, "percentage": 23}],\n        active_zone_progress=23,\n        active_progress_zone_id=24,\n    )\n    manager.update_zone_history(\n        {"zones": [{"id": 24, "pct": 97}]},\n        [{"id": 24, "name": "Yard", "progress": 97, "percentage": 23}],\n        active_zone_progress=97,\n        active_progress_zone_id=24,\n    )\n''',
    "dock evidence regression")
with test_path.open("a", encoding="utf-8") as handle:
    handle.write(dedent(r'''

async def beta18_stale_high_error_return_test() -> None:
    Store.values.clear()
    hass = FakeHass()
    manager = history.NavimowerHistory(hass, "entry-beta18-error", "TEST")
    manager.process_pose(position={"x": 5.0, "y": 5.0}, pose_time=2_700_000_000, heading=0.0,
        activity="mowing", cutting=True, docked=False, returning=False, zone_ids=[24], physical_zone_id=24)
    manager.update_zone_history(
        {"zones": [{"id": 24, "pct": 98}]},
        [{"id": 24, "name": "Yard", "progress": 98, "percentage": 98,
          "last_completed_at": history._iso(2_700_000_010_000)}],
        active_zone_progress=98, active_progress_zone_id=24)
    active = manager.active_session
    assert active and active["completed"] is not True
    assert active["task_zone_completion_confirmed"] == []
    assert "last_completed_at" not in manager.zone_history()["24"]
    manager.process_pose(position={"x": 5.1, "y": 5.1}, pose_time=2_700_000_020, heading=0.0,
        activity="docked", cutting=False, docked=True, returning=False, zone_ids=[24], completed=True)
    assert "last_completed_at" not in manager.zone_history()["24"]
    assert manager.session_summaries(include_points=True)[-1]["completed"] is False
    if hass.tasks:
        await asyncio.gather(*hass.tasks)

asyncio.run(beta18_stale_high_error_return_test())

async def beta18_persisted_false_completion_repair_test() -> None:
    Store.values.clear()
    reset_ms = 2_800_000_020_000
    reset_session = session("1", 2_800_000_000_000, 2_800_000_030_000)
    reset_session["zone_ids"] = [24]
    reset_session["zone_cycle_boundaries"] = [{"zone_id": 24, "at_ms": reset_ms, "reason": "vendor_progress_reset"}]
    Store.values["navimower_sessions_entry-beta18-repair"] = {
        "sn": "TEST", "sequence": 1, "active_id": None,
        "sessions": [history._metadata(reset_session)],
        "zone_history": {
            "13": {"id": 13, "last_completed_at": history._iso(2_800_000_010_000)},
            "24": {"id": 24, "last_completed_at": history._iso(reset_ms), "last_completed_progress": 98},
            "42": {"id": 42, "last_completed_at": history._iso(2_799_000_000_000), "last_completed_progress": 97},
        },
    }
    Store.values["navimower_session_entry-beta18-repair_1"] = reset_session
    manager = history.NavimowerHistory(FakeHass(), "entry-beta18-repair", "TEST")
    await manager.async_load()
    repaired = manager.zone_history()
    assert "last_completed_at" not in repaired["13"]
    assert "last_completed_at" not in repaired["24"]
    assert repaired["42"]["last_completed_progress"] == 97
    assert repaired["42"]["last_completed_at"]

asyncio.run(beta18_persisted_false_completion_repair_test())
print("beta18 completion safety tests passed")
'''))

beta17_path = ROOT / "tests" / "test_v043_beta17.py"
replace_once(beta17_path,
    '''def test_beta17_identity():\n    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))\n    assert manifest["version"] == "0.4.3-beta17"\n    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta17.md").read_text(encoding="utf-8")\n    assert notes.startswith("title: Navimower 0.4.3-beta17")\n''',
    '''def test_beta17_release_notes_remain_available():\n    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta17.md").read_text(encoding="utf-8")\n    assert notes.startswith("title: Navimower 0.4.3-beta17")\n''',
    "beta17 historical identity")
write(ROOT / "tests" / "test_v043_beta18.py", r'''
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta18_identity():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta18"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta18.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta18")


def test_completion_is_current_cycle_confirmed():
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    assert '"vendor_completed_at": (' in coordinator
    assert '"last_completed_at": None' in coordinator
    assert '"task_zone_seen_incomplete"' in history
    assert '"task_zone_completion_confirmed"' in history
    assert "_async_repair_unverified_zone_completions" in history
    start = history.index("    def prepare_cycle(\n")
    end = history.index("    def cycle_diagnostics", start)
    assert '"last_completed_at"' not in history[start:end]
''')

changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
marker = "# Changelog\n\n\n"
if not changelog.startswith(marker):
    raise SystemExit("Unexpected changelog header")
section = '''## 0.4.3-beta18\n\nConfirmed per-zone completion tracking.\n\n### Fixed\n\n- Do not advance zone `last_completed` when a task starts with stale high progress and then immediately fails/returns to the dock.\n- Do not stamp `last_completed` at a vendor progress-reset/new-cycle boundary; that timestamp is the next cycle start, not the previous cycle completion.\n- Stop treating private-cloud `end_time + >=95%` as authoritative completion evidence.\n- Repair persisted beta-era completion timestamps that were unverified vendor values or matched a recorded reset boundary.\n\n### Safety\n\n- Completion requires current-cycle evidence: the zone must first be observed below the completion threshold and later at or above the existing 95% threshold.\n- Failed/error returns can close the route session without making an unconfirmed zone eligible for Navimower Schedule.\n\n\n'''
changelog_path.write_text(marker + section + changelog[len(marker):], encoding="utf-8")
write(ROOT / ".github" / "release-notes" / "0.4.3-beta18.md", r'''
title: Navimower 0.4.3-beta18

Confirmed per-zone completion tracking.

### Fixed
- Zone **Last completed** no longer advances when a new task inherits stale high/100% progress and then immediately fails or returns to the dock.
- A vendor progress reset/new-cycle start is only a cycle boundary; its start timestamp is no longer written as the previous cycle's completion.
- Private-cloud `end_time + >=95%` remains diagnostic evidence but is no longer authoritative by itself.
- On load, beta-era completion timestamps are removed when they were unverified vendor values or matched a recorded reset boundary.

### Safety
- Completion requires current-cycle evidence: Navimower must first observe the zone below the completion threshold and later at or above the existing **95%** threshold.
- Error/failed returns cannot make an unconfirmed zone eligible for **Navimower Schedule**.

### Unchanged
- Navimower Schedule still requires explicit user selection and a confirmed successful completion before automatic mowing.
- Maintenance / Mowing Reports and Clear and resume / Reboot command discovery remain unchanged/read-only.
''')
