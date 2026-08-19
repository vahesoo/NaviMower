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


def replace_between(path: Path, start: str, end: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    start_at = text.find(start)
    if start_at < 0:
        raise SystemExit(f"{label}: start marker not found")
    end_at = text.find(end, start_at)
    if end_at < 0:
        raise SystemExit(f"{label}: end marker not found")
    path.write_text(text[:start_at] + dedent(new).lstrip("\n") + text[end_at:], encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).lstrip(), encoding="utf-8")


# Identity.
manifest_path = COMPONENT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != "0.4.3-beta20":
    raise SystemExit(f"Expected beta20 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta21"
manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

history_path = COMPONENT / "history.py"

# Completion authority: only fresh vendor per-zone coverage may write Last completed.
replace_once(
    history_path,
    '''# Completion is deliberately stricter than display telemetry. Last-known values\n# can keep sensors stable but can never create a completion event. Fresh MQTT or\n# private-cloud evidence must belong to the current zone/cycle.\n_COMPLETION_EVIDENCE_MAX_AGE_SECONDS = 30.0\n_COMPLETION_SAMPLE_ADVANCE_MS = 1000\n_COMPLETION_JUMP_GUARD_PERCENT = 25\n_COMPLETION_CYCLE_START_TOLERANCE_MS = 30_000\n_ACTIVE_ZONE_COMPLETION_SOURCES = frozenset(\n    {\n        "mqtt_map_work_position",\n        "mqtt_route_progress",\n        "private_map_work_position",\n    }\n)\n_SINGLE_ZONE_TASK_COMPLETION_SOURCES = frozenset(\n    {"mqtt_task_percentage", "private_task_percentage"}\n)\n''',
    '''# Completion is stricter than display telemetry. Live work/task counters are\n# useful progress signals, but only fresh vendor per-zone coverage is allowed to\n# advance ``last_completed_at``. This avoids stale mapWorkPosition/task values\n# falsely completing a zone after rain, charging or another interruption.\n_COMPLETION_EVIDENCE_MAX_AGE_SECONDS = 30.0\n_COMPLETION_VENDOR_EVENT_TOLERANCE_MS = 120_000\n''',
    "completion authority constants",
)

# Preserve coverage-cycle state when two short session fragments are merged.
replace_once(
    history_path,
    '''    merged["task_zone_seen_incomplete"] = _unique_ints(\n        previous.get("task_zone_seen_incomplete"), continuation.get("task_zone_seen_incomplete")\n    )\n    merged["task_zone_completion_confirmed"] = _unique_ints(\n        previous.get("task_zone_completion_confirmed"), continuation.get("task_zone_completion_confirmed")\n    )\n''',
    '''    merged["task_zone_seen_incomplete"] = _unique_ints(\n        previous.get("task_zone_seen_incomplete"), continuation.get("task_zone_seen_incomplete")\n    )\n    merged["task_zone_seen_target"] = _unique_ints(\n        previous.get("task_zone_seen_target"), continuation.get("task_zone_seen_target")\n    )\n    coverage_state = dict(previous.get("task_zone_coverage_state") or {})\n    coverage_state.update(continuation.get("task_zone_coverage_state") or {})\n    merged["task_zone_coverage_state"] = coverage_state\n    merged["task_zone_completion_confirmed"] = _unique_ints(\n        previous.get("task_zone_completion_confirmed"), continuation.get("task_zone_completion_confirmed")\n    )\n''',
    "merge coverage completion state",
)

replace_once(
    history_path,
    '''            "task_zone_seen_incomplete": [],\n            "task_zone_completion_confirmed": [],\n            "task_zone_last_evidence": {},\n            "task_zone_completion_candidates": {},\n''',
    '''            "task_zone_seen_incomplete": [],\n            "task_zone_seen_target": [],\n            "task_zone_coverage_state": {},\n            "task_zone_completion_confirmed": [],\n            "task_zone_last_evidence": {},\n            "task_zone_completion_candidates": {},\n''',
    "new session coverage state",
)

replace_once(
    history_path,
    '''        previous.setdefault("task_zone_seen_incomplete", [])\n        previous.setdefault("task_zone_completion_confirmed", [])\n        previous.setdefault("task_zone_last_evidence", {})\n''',
    '''        previous.setdefault("task_zone_seen_incomplete", [])\n        previous.setdefault("task_zone_seen_target", [])\n        previous.setdefault("task_zone_coverage_state", {})\n        previous.setdefault("task_zone_completion_confirmed", [])\n        previous.setdefault("task_zone_last_evidence", {})\n''',
    "resume coverage state",
)

# A reset starts a new vendor coverage cycle; stale per-zone coverage evidence
# from the old cycle must not leak into the new one.
replace_once(
    history_path,
    '''                task_progress = active.setdefault("task_zone_progress", {})\n                last_evidence = active.setdefault("task_zone_last_evidence", {})\n                completion_candidates = active.setdefault(\n                    "task_zone_completion_candidates", {}\n                )\n                for zone_id in reset_set:\n                    task_progress[str(zone_id)] = 0\n                    last_evidence.pop(str(zone_id), None)\n                    completion_candidates.pop(str(zone_id), None)\n''',
    '''                task_progress = active.setdefault("task_zone_progress", {})\n                last_evidence = active.setdefault("task_zone_last_evidence", {})\n                coverage_state = active.setdefault("task_zone_coverage_state", {})\n                completion_candidates = active.setdefault(\n                    "task_zone_completion_candidates", {}\n                )\n                for zone_id in reset_set:\n                    task_progress[str(zone_id)] = 0\n                    last_evidence.pop(str(zone_id), None)\n                    coverage_state.pop(str(zone_id), None)\n                    completion_candidates.pop(str(zone_id), None)\n''',
    "reset coverage state",
)

# Run the coverage authority while the active session still exists. This is
# deliberately before process_pose can close the session on a docked snapshot.
replace_once(
    history_path,
    '''                self._zone_progress_state[str(zone_id)] = {"progress": progress, "peak_progress": peak, "start_time": _as_int(row.get("start_time")), "end_time": _as_int(row.get("end_time")), "observed_at_ms": _timestamp_ms(pose_time)}\n        self._schedule_index_save()\n        return bool(reset_rows)\n\n    def cycle_diagnostics(self) -> dict[str, Any]:\n''',
    '''                self._zone_progress_state[str(zone_id)] = {"progress": progress, "peak_progress": peak, "start_time": _as_int(row.get("start_time")), "end_time": _as_int(row.get("end_time")), "observed_at_ms": _timestamp_ms(pose_time)}\n            self._confirm_coverage_completions_locked(snapshot, pose_time)\n        self._schedule_index_save()\n        return bool(reset_rows)\n\n    def _confirm_coverage_completions_locked(\n        self, snapshot: dict[str, Any], pose_time: Any\n    ) -> None:\n        """Confirm per-zone completion only from fresh vendor coverage reaching 100%."""\n        active = self._cache.get(self._active_id or "")\n        if active is None:\n            return\n\n        coverage_age = _as_float(snapshot.get("coverage_source_age"))\n        if (\n            coverage_age is None\n            or coverage_age < 0\n            or coverage_age > _COMPLETION_EVIDENCE_MAX_AGE_SECONDS\n        ):\n            return\n\n        coverage_rows = {\n            _as_int(item.get("id")): item\n            for item in (snapshot.get("coverage") or {}).get("zones") or []\n            if isinstance(item, dict) and _as_int(item.get("id")) is not None\n        }\n        if not coverage_rows:\n            return\n\n        now_ms = _timestamp_ms(pose_time)\n        current_target = _as_int(snapshot.get("active_zone_progress_zone_id"))\n        physical_zone = _as_int(snapshot.get("current_physical_zone_id"))\n        activity_name = str(snapshot.get("activity") or "").lower()\n\n        seen_target = set(_unique_ints(active.get("task_zone_seen_target")))\n        if current_target is not None:\n            seen_target.add(current_target)\n        if activity_name == "mowing" and physical_zone is not None:\n            seen_target.add(physical_zone)\n\n        seen_incomplete = set(\n            _unique_ints(active.get("task_zone_seen_incomplete"))\n        )\n        confirmed = set(\n            _unique_ints(active.get("task_zone_completion_confirmed"))\n        )\n        coverage_state = active.setdefault("task_zone_coverage_state", {})\n        last_evidence = active.setdefault("task_zone_last_evidence", {})\n        completion_candidates = active.setdefault(\n            "task_zone_completion_candidates", {}\n        )\n\n        relevant = sorted(seen_target | seen_incomplete)\n        for zone_id in relevant:\n            row = coverage_rows.get(zone_id)\n            if not isinstance(row, dict):\n                continue\n            progress = _as_int(row.get("pct"))\n            if progress is None or progress < 0 or progress > 100:\n                continue\n\n            zone_key = str(zone_id)\n            previous = dict(coverage_state.get(zone_key) or {})\n            previous_progress = _as_int(previous.get("progress"))\n            previous_start = _as_int(previous.get("start_time"))\n            previous_end = _as_int(previous.get("end_time"))\n            vendor_start = _as_int(row.get("start_time"))\n            vendor_end = _as_int(row.get("end_time"))\n            vendor_start_ms = _timestamp_ms(vendor_start) if vendor_start else None\n            vendor_end_ms = _timestamp_ms(vendor_end) if vendor_end else None\n            sample_ms = now_ms - int(max(0.0, coverage_age) * 1000)\n            evidence = {\n                "progress": progress,\n                "source": "private_zone_coverage",\n                "source_age_s": round(coverage_age, 3),\n                "sample_ms": sample_ms,\n                "observed_at": _iso(now_ms),\n                "start_time": vendor_start,\n                "end_time": vendor_end,\n            }\n\n            if progress < 100:\n                seen_incomplete.add(zone_id)\n                completion_candidates.pop(zone_key, None)\n                active["last_completion_rejection"] = None\n                last_evidence[zone_key] = evidence\n                coverage_state[zone_key] = {\n                    "progress": progress,\n                    "start_time": vendor_start,\n                    "end_time": vendor_end,\n                    "observed_at_ms": now_ms,\n                }\n                continue\n\n            if zone_id in confirmed:\n                coverage_state[zone_key] = {\n                    "progress": progress,\n                    "start_time": vendor_start,\n                    "end_time": vendor_end,\n                    "observed_at_ms": now_ms,\n                }\n                last_evidence[zone_key] = evidence\n                continue\n\n            transitioned_to_100 = bool(\n                previous_progress is not None and previous_progress < 100\n            )\n            vendor_cycle_changed = bool(\n                (\n                    vendor_start is not None\n                    and previous_start is not None\n                    and vendor_start > previous_start\n                )\n                or (\n                    vendor_end is not None\n                    and previous_end is not None\n                    and vendor_end > previous_end\n                    and previous_progress is not None\n                    and previous_progress < 100\n                )\n            )\n\n            zone_record = dict(self._zone_history.get(zone_key) or {})\n            previous_completed_ms = _iso_ms(zone_record.get("last_completed_at"))\n            recent_vendor_cycle = bool(\n                zone_id in seen_target\n                and vendor_start_ms is not None\n                and vendor_end_ms is not None\n                and vendor_end_ms >= vendor_start_ms\n                and vendor_start_ms\n                >= now_ms - _COMPLETION_VENDOR_EVENT_TOLERANCE_MS\n                and vendor_end_ms\n                <= now_ms + _COMPLETION_VENDOR_EVENT_TOLERANCE_MS\n                and (\n                    previous_completed_ms is None\n                    or vendor_end_ms > previous_completed_ms + 1000\n                )\n            )\n\n            if zone_id in seen_incomplete:\n                confirmation = "coverage_100_after_incomplete"\n            elif transitioned_to_100 or vendor_cycle_changed:\n                confirmation = "coverage_100_transition"\n            elif recent_vendor_cycle:\n                confirmation = "coverage_100_recent_vendor_cycle"\n            else:\n                completion_candidates[zone_key] = {\n                    **evidence,\n                    "zone_id": zone_id,\n                    "reason": "coverage_100_without_current_cycle_evidence",\n                }\n                active["last_completion_rejection"] = dict(\n                    completion_candidates[zone_key]\n                )\n                coverage_state[zone_key] = {\n                    "progress": progress,\n                    "start_time": vendor_start,\n                    "end_time": vendor_end,\n                    "observed_at_ms": now_ms,\n                }\n                last_evidence[zone_key] = evidence\n                continue\n\n            completion_ms = now_ms\n            armed_sample_ms = _as_int(\n                (last_evidence.get(zone_key) or {}).get("sample_ms")\n            )\n            if (\n                vendor_end_ms is not None\n                and vendor_end_ms\n                <= now_ms + _COMPLETION_VENDOR_EVENT_TOLERANCE_MS\n                and (\n                    armed_sample_ms is None\n                    or vendor_end_ms\n                    >= armed_sample_ms - _COMPLETION_VENDOR_EVENT_TOLERANCE_MS\n                )\n            ):\n                completion_ms = vendor_end_ms\n\n            confirmed.add(zone_id)\n            completion_candidates.pop(zone_key, None)\n            active["last_completion_rejection"] = None\n            zone_record.update(\n                {\n                    "id": zone_id,\n                    "name": zone_record.get("name")\n                    or row.get("name")\n                    or f"Zone {zone_id}",\n                    "last_completed_at": _iso(completion_ms),\n                    "last_completed_progress": 100,\n                    "last_completed_source": "private_zone_coverage",\n                    "last_completed_confirmation": confirmation,\n                    "last_completed_cycle_id": active.get("id"),\n                }\n            )\n            self._zone_history[zone_key] = zone_record\n            active.setdefault("final_progress", {})[zone_key] = 100\n            coverage_state[zone_key] = {\n                "progress": progress,\n                "start_time": vendor_start,\n                "end_time": vendor_end,\n                "observed_at_ms": now_ms,\n            }\n            last_evidence[zone_key] = evidence\n\n        active["task_zone_seen_target"] = sorted(seen_target)\n        active["task_zone_seen_incomplete"] = sorted(seen_incomplete)\n        active["task_zone_completion_confirmed"] = sorted(confirmed)\n        selected = _unique_ints(active.get("zone_ids"))\n        if selected and set(selected).issubset(confirmed):\n            active["completed"] = True\n            active["completion_reason"] = "vendor_coverage"\n        elif active.get("completion_reason") == "vendor_coverage":\n            active["completed"] = None\n            active["completion_reason"] = None\n        self._update_active_metadata_locked(active)\n        self._schedule_active_save()\n\n    def cycle_diagnostics(self) -> dict[str, Any]:\n''',
    "coverage completion hook",
)

replace_once(
    history_path,
    '''                    "seen_incomplete": _unique_ints(\n                        active.get("task_zone_seen_incomplete")\n                    ),\n                    "confirmed": _unique_ints(\n''',
    '''                    "seen_incomplete": _unique_ints(\n                        active.get("task_zone_seen_incomplete")\n                    ),\n                    "seen_target": _unique_ints(\n                        active.get("task_zone_seen_target")\n                    ),\n                    "coverage_state": deepcopy(\n                        active.get("task_zone_coverage_state") or {}\n                    ),\n                    "confirmed": _unique_ints(\n''',
    "coverage completion diagnostics",
)

# Remove beta20 live-counter completion arbitration. Zone display state still
# uses the normal selected progress source; Last completed does not.
replace_between(
    history_path,
    "    def update_zone_history(\n",
    "    def update_from_snapshot(\n",
    r'''
    def update_zone_history(
        self,
        coverage: dict[str, Any] | None,
        zone_details: list[dict[str, Any]],
        *,
        active_zone_progress: Any = None,
        active_progress_zone_id: Any = None,
        active_zone_progress_source: Any = None,
        active_zone_progress_source_age: Any = None,
        task_progress: Any = None,
        task_progress_source: Any = None,
        task_progress_source_age: Any = None,
        target_zone_ids: Any = None,
        physical_zone_id: Any = None,
        coverage_source_age: Any = None,
        activity: Any = None,
        cycle_reset_pending: bool = False,
        observed_at: Any = None,
    ) -> None:
        """Persist display state; completion is owned by vendor coverage arbitration."""
        coverage_by_id = {
            _as_int(item.get("id")): item
            for item in (coverage or {}).get("zones") or []
            if isinstance(item, dict) and _as_int(item.get("id")) is not None
        }
        changed = False
        with self._lock:
            for detail in zone_details:
                if not isinstance(detail, dict):
                    continue
                zone_id = _as_int(detail.get("id"))
                if zone_id is None:
                    continue
                row = coverage_by_id.get(zone_id) or {}
                record = dict(self._zone_history.get(str(zone_id)) or {})
                record.update(
                    {
                        "id": zone_id,
                        "name": detail.get("name") or f"Zone {zone_id}",
                        "area_m2": detail.get("area_m2", row.get("area")),
                        "finished_area_m2": detail.get(
                            "finished_area_m2", row.get("finished")
                        ),
                        "percentage": (
                            detail.get("progress")
                            if detail.get("progress") is not None
                            else detail.get("percentage", row.get("pct"))
                        ),
                        "vendor_percentage": detail.get(
                            "vendor_percentage", row.get("pct")
                        ),
                        "progress_source": detail.get("progress_source") or "coverage",
                        "cutting_height_mm": detail.get("cutting_height_mm"),
                        "inherits_global_height": detail.get(
                            "inherits_global_height"
                        ),
                    }
                )
                for key in ("last_started_at", "last_mowed_at"):
                    value = detail.get(key)
                    if value:
                        previous = record.get(key)
                        record[key] = max(str(previous or ""), str(value))
                active = self._cache.get(self._active_id or "")
                if active is not None and zone_id in set(
                    active.get("visited_zone_ids") or []
                ):
                    record["cycle_id"] = active.get("id")
                self._zone_history[str(zone_id)] = record
                changed = True

            active = self._cache.get(self._active_id or "")
            if active is not None:
                if active.get("cutting_height_mm") is None:
                    target_ids = set(active.get("zone_ids") or [])
                    candidates = [
                        _as_int(item.get("cutting_height_mm"))
                        for item in zone_details
                        if not target_ids or _as_int(item.get("id")) in target_ids
                    ]
                    known = [value for value in candidates if value is not None]
                    if len(set(known)) == 1:
                        active["cutting_height_mm"] = known[0]
                self._update_active_metadata_locked(active)
                changed = True

        if changed:
            self._schedule_index_save()
            self._schedule_active_save()

''',
    "replace live completion arbitration",
)

# beta20 remains a historical regression suite after beta21 takes identity and
# its old arbitration markers are intentionally gone.
write(ROOT / "tests" / "test_v043_beta20.py", r'''
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta20_release_notes_remain_available():
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta20.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta20")


def test_navimower_schedule_dispatches_one_zone_at_a_time():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    send = source[source.index("    async def _async_send_mow"):]
    assert "encode_partition_ids([zone_id])" in send
    assert "requested_zone_ids=[zone_id]" in send
    assert "resolved_zone_ids=[zone_id]" in send
    assert "completion_advanced(" in source


def test_docking_only_finalizes_history():
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    dock_start = history.index("            if docked and not cutting:")
    dock_end = history.index("    # Backward-compatible alias", dock_start)
    dock = history[dock_start:dock_end]
    assert "_finish_active_locked" in dock
    assert "last_completed_at" not in dock
    assert "completed=self._session_completed(snapshot) if docked else None" not in coordinator
    assert "completed=None" in coordinator


def test_completion_metadata_and_freshness_diagnostics_remain_exposed():
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    zone_state = (COMPONENT / "zone_state.py").read_text(encoding="utf-8")
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    assert '"active_zone_progress_source_age"' in diagnostics
    assert '"coverage_source_age"' in diagnostics
    assert '"active_completion"' in history
    assert '"last_completed_source"' in zone_state
    assert '"last_completed_confirmation"' in zone_state
''')

write(ROOT / "tests" / "test_v043_beta21.py", r'''
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta21_identity():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta21"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta21.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta21")


def test_last_completed_is_vendor_zone_coverage_authoritative():
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    start = history.index("    def _confirm_coverage_completions_locked")
    end = history.index("    def cycle_diagnostics", start)
    completion = history[start:end]
    assert 'row.get("pct")' in completion
    assert '"private_zone_coverage"' in completion
    assert 'if progress < 100:' in completion
    assert '"coverage_100_after_incomplete"' in completion
    assert '"coverage_100_transition"' in completion
    assert '"coverage_100_recent_vendor_cycle"' in completion
    assert '"coverage_100_without_current_cycle_evidence"' in completion
    assert '"last_completed_progress": 100' in completion
    assert '"last_completed_cycle_id": active.get("id")' in completion
    assert "mqtt_map_work_position" not in completion
    assert "mqtt_task_percentage" not in completion
    assert "current_cycle_cloud_end" not in history
    assert "waiting_for_second_fresh_sample" not in history


def test_target_transition_keeps_previous_zone_armed_until_coverage_100():
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    start = history.index("    def _confirm_coverage_completions_locked")
    end = history.index("    def cycle_diagnostics", start)
    completion = history[start:end]
    assert 'seen_target.add(current_target)' in completion
    assert 'seen_target.add(physical_zone)' in completion
    assert 'relevant = sorted(seen_target | seen_incomplete)' in completion
    assert 'seen_incomplete.add(zone_id)' in completion
    assert 'active["task_zone_seen_target"] = sorted(seen_target)' in completion
    assert 'active["task_zone_seen_incomplete"] = sorted(seen_incomplete)' in completion


def test_private_live_100_and_task_100_cannot_directly_complete():
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    update_start = history.index("    def update_zone_history")
    update_end = history.index("    def update_from_snapshot", update_start)
    update = history[update_start:update_end]
    assert "last_completed_at" not in update
    assert "active_zone_progress_source" in update
    assert "task_progress_source" in update
    assert "completion is owned by vendor coverage arbitration" in update


def test_cloud_end_time_is_timestamp_only_after_coverage_100():
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    start = history.index("    def _confirm_coverage_completions_locked")
    end = history.index("    def cycle_diagnostics", start)
    completion = history[start:end]
    progress_gate = completion.index("if progress < 100:")
    completion_write = completion.index('"last_completed_at"')
    end_time_use = completion.index("vendor_end_ms")
    assert progress_gate < completion_write
    assert end_time_use < completion_write
    assert "end_time + >=95" not in completion
''')

changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
marker = "# Changelog\n\n\n"
if not changelog.startswith(marker):
    raise SystemExit("Unexpected changelog header")
section = '''## 0.4.3-beta21\n\nVendor per-zone coverage becomes the sole authority for `Last completed`.\n\n### Fixed\n\n- Stop allowing MQTT/private `mapWorkPosition`, route progress or whole-task percentage to write `last_completed`; these remain display/progress signals only.\n- Confirm a zone only when fresh private-cloud per-zone coverage for a currently observed target/physical zone reaches 100% in the current work cycle.\n- Keep an incomplete zone armed after the mower changes target, so multi-zone tasks can confirm the previous zone when its vendor coverage settles to 100%.\n- Ignore stale historical 100% rows until the zone has current-cycle evidence; this prevents rain/charging resumes from completing a still-partial zone.\n\n### Safety\n\n- `endTime` is never completion proof by itself. It is used only as the timestamp after coverage has already reached authoritative 100%.\n- A small-zone fallback accepts a directly observed recent 100% vendor cycle only when its start/end timestamps are new relative to the previously persisted completion.\n- Docking still only finalizes route/session history and does not create zone completion.\n\n### Diagnostics\n\n- Expose zones seen as current targets plus retained vendor coverage state in active completion diagnostics.\n\n\n'''
changelog_path.write_text(marker + section + changelog[len(marker):], encoding="utf-8")

write(ROOT / ".github" / "release-notes" / "0.4.3-beta21.md", r'''
title: Navimower 0.4.3-beta21

Per-zone `Last completed` is now driven by vendor zone coverage instead of live work/task counters.

### Fixed
- Fresh private-cloud **per-zone coverage at 100%** is the only telemetry allowed to write `last_completed`.
- MQTT/private `mapWorkPosition`, route progress and whole-task percentage remain useful live progress sources, but cannot directly mark a zone complete.
- A zone that was observed below 100% stays armed after the mower changes to another target. This allows both single-zone schedulers and native multi-zone tasks to confirm the previous zone when cloud coverage settles to 100%.
- Stale 100% rows from a previous cycle are ignored until there is current-cycle evidence. This specifically prevents a rain/charging resume from immediately completing a still-partial zone because `mapWorkPosition` temporarily retained 100%.

### Safety
- Vendor `endTime` is timestamp metadata only. It can timestamp an already-confirmed 100% completion, but never proves completion by itself.
- Very small zones that can move from start to 100% between polls have a bounded recent-cycle fallback using vendor start/end timestamps, and the vendor end must be newer than the previously persisted completion.
- Dock/Charging still only closes the route/session history; it does not write `Last completed`.

### Compatibility
- The same `last_completed` sensor remains the contract for Navimower Schedule and external integrations such as `navimower-zone-scheduler`.
- The completion resolver is scheduler-independent and works with one-zone commands as well as native multi-zone mowing.

### Diagnostics
- Active completion diagnostics now include zones seen as current targets and retained per-zone vendor coverage state.
''')
