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
    path.write_text(text[:start_at] + new + text[end_at:], encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).lstrip(), encoding="utf-8")


# ---------------------------------------------------------------- identity
manifest_path = COMPONENT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != "0.4.3-beta19":
    raise SystemExit(f"Expected beta19 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta20"
manifest_path.write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)

# ----------------------------------------------------------- coordinator glue
coordinator_path = COMPONENT / "coordinator.py"
replace_once(
    coordinator_path,
    '''        snapshot["coverage"] = coverage\n        snapshot["coverage_source"] = "private_cloud" if coverage is not None else None\n        self._coverage_reset_pending = False\n''',
    '''        snapshot["coverage"] = coverage\n        snapshot["coverage_source"] = "private_cloud" if coverage is not None else None\n        snapshot["coverage_source_age"] = (\n            self._private_endpoint_age("path_info_time")\n            if coverage is not None\n            else None\n        )\n        self._coverage_reset_pending = False\n''',
    "coverage freshness",
)
replace_once(
    coordinator_path,
    '''            completed=self._session_completed(snapshot) if docked else None,\n''',
    '''            # Zone completion is confirmed from current-cycle telemetry in\n            # history.update_from_snapshot(). Docking only closes the route session.\n            completed=None,\n''',
    "remove dock completion coupling",
)

# -------------------------------------------------------------- history core
history_path = COMPONENT / "history.py"
replace_once(
    history_path,
    '''    DOMAIN,\n    MQTT_HISTORY_SAVE_DELAY_SECONDS,\n''',
    '''    ACTIVITY_ERROR,\n    ACTIVITY_RETURNING,\n    DOMAIN,\n    MQTT_HISTORY_SAVE_DELAY_SECONDS,\n''',
    "history activity imports",
)
replace_once(
    history_path,
    '''_LEGACY_TRAIL_VERSION = 1\n\nSESSION_DETAIL_POINT_FORMAT = [\n''',
    '''_LEGACY_TRAIL_VERSION = 1\n\n# Completion is deliberately stricter than display telemetry. Last-known values\n# can keep sensors stable but can never create a completion event. Fresh MQTT or\n# private-cloud evidence must belong to the current zone/cycle.\n_COMPLETION_EVIDENCE_MAX_AGE_SECONDS = 30.0\n_COMPLETION_SAMPLE_ADVANCE_MS = 1000\n_COMPLETION_JUMP_GUARD_PERCENT = 25\n_COMPLETION_CYCLE_START_TOLERANCE_MS = 120_000\n_ACTIVE_ZONE_COMPLETION_SOURCES = frozenset(\n    {\n        "mqtt_map_work_position",\n        "mqtt_route_progress",\n        "private_map_work_position",\n    }\n)\n_SINGLE_ZONE_TASK_COMPLETION_SOURCES = frozenset(\n    {"mqtt_task_percentage", "private_task_percentage"}\n)\n\nSESSION_DETAIL_POINT_FORMAT = [\n''',
    "completion constants",
)
replace_once(
    history_path,
    '''            "task_zone_progress": {str(value): 0 for value in zone_ids},\n            "task_zone_seen_incomplete": [],\n            "task_zone_completion_confirmed": [],\n            "segment_starts_ms": [start_ms],\n''',
    '''            "task_zone_progress": {str(value): 0 for value in zone_ids},\n            "task_zone_seen_incomplete": [],\n            "task_zone_completion_confirmed": [],\n            "task_zone_last_evidence": {},\n            "task_zone_completion_candidates": {},\n            "last_completion_rejection": None,\n            "segment_starts_ms": [start_ms],\n''',
    "session completion state",
)
replace_once(
    history_path,
    '''        previous.setdefault("task_zone_progress", {})\n        previous.setdefault("task_zone_seen_incomplete", [])\n        previous.setdefault("task_zone_completion_confirmed", [])\n        previous["zone_ids"] = _unique_ints(\n''',
    '''        previous.setdefault("task_zone_progress", {})\n        previous.setdefault("task_zone_seen_incomplete", [])\n        previous.setdefault("task_zone_completion_confirmed", [])\n        previous.setdefault("task_zone_last_evidence", {})\n        previous.setdefault("task_zone_completion_candidates", {})\n        previous.setdefault("last_completion_rejection", None)\n        previous["zone_ids"] = _unique_ints(\n''',
    "resume completion state",
)
replace_once(
    history_path,
    '''                task_progress = active.setdefault("task_zone_progress", {})\n                for zone_id in reset_set:\n                    task_progress[str(zone_id)] = 0\n                if active.get("completion_reason") == "vendor_progress":\n''',
    '''                task_progress = active.setdefault("task_zone_progress", {})\n                last_evidence = active.setdefault("task_zone_last_evidence", {})\n                completion_candidates = active.setdefault(\n                    "task_zone_completion_candidates", {}\n                )\n                for zone_id in reset_set:\n                    task_progress[str(zone_id)] = 0\n                    last_evidence.pop(str(zone_id), None)\n                    completion_candidates.pop(str(zone_id), None)\n                if active.get("completion_reason") == "vendor_progress":\n''',
    "reset completion evidence",
)

# Docking finalizes route/session history only. Per-zone completion is already
# persisted when fresh current-cycle telemetry confirms it.
replace_between(
    history_path,
    '''            if docked and not cutting:\n''',
    '''\n    # Backward-compatible alias used by older internal experiments.\n''',
    '''            if docked and not cutting:\n                self._finish_active_locked(pose_time)\n            else:\n                self._update_active_metadata_locked(active)\n                self._schedule_active_save()\n                self._schedule_index_save()\n''',
    "dock finalization",
)

# Diagnostics now expose the current completion arbitration state.
replace_between(
    history_path,
    '''    def cycle_diagnostics(self) -> dict[str, Any]:\n''',
    '''\n    # ----------------------------------------------------------- zone history\n''',
    '''    def cycle_diagnostics(self) -> dict[str, Any]:\n        """Return non-sensitive cycle and completion-arbitration state."""\n        with self._lock:\n            active = self._cache.get(self._active_id or "")\n            active_completion = None\n            if active is not None:\n                active_completion = {\n                    "session_id": active.get("id"),\n                    "zone_ids": _unique_ints(active.get("zone_ids")),\n                    "seen_incomplete": _unique_ints(\n                        active.get("task_zone_seen_incomplete")\n                    ),\n                    "confirmed": _unique_ints(\n                        active.get("task_zone_completion_confirmed")\n                    ),\n                    "last_evidence": deepcopy(\n                        active.get("task_zone_last_evidence") or {}\n                    ),\n                    "candidates": deepcopy(\n                        active.get("task_zone_completion_candidates") or {}\n                    ),\n                    "last_rejection": deepcopy(\n                        active.get("last_completion_rejection")\n                    ),\n                }\n            return {\n                "last_event": deepcopy(self._last_cycle_event),\n                "zone_progress_state": deepcopy(self._zone_progress_state),\n                "force_new_session_once": self._force_new_session_once,\n                "force_new_cycle_zone_ids": list(self._force_new_cycle_zone_ids),\n                "active_completion": active_completion,\n            }\n''',
    "cycle diagnostics",
)

new_zone_history = r'''
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
        """Persist zone state and confirm completion from fresh current-cycle evidence.

        Display telemetry may retain a last-known value through a source outage.
        Completion is intentionally fail-closed: only fresh MQTT/private-cloud
        counters tied to the active zone/cycle may advance ``last_completed_at``.
        """
        coverage_by_id = {
            _as_int(item.get("id")): item
            for item in (coverage or {}).get("zones") or []
            if isinstance(item, dict) and _as_int(item.get("id")) is not None
        }
        changed = False
        now_ms = _timestamp_ms(observed_at)

        def fresh_age(value: Any) -> tuple[bool, float | None]:
            age = _as_float(value)
            return (
                age is not None
                and 0 <= age <= _COMPLETION_EVIDENCE_MAX_AGE_SECONDS,
                age,
            )

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
                progress_state = active.setdefault("task_zone_progress", {})
                for zone_id in active.get("zone_ids") or []:
                    progress_state.setdefault(str(zone_id), 0)
                seen_incomplete = set(
                    _unique_ints(active.get("task_zone_seen_incomplete"))
                )
                completion_confirmed = set(
                    _unique_ints(active.get("task_zone_completion_confirmed"))
                )
                last_evidence = active.setdefault("task_zone_last_evidence", {})
                completion_candidates = active.setdefault(
                    "task_zone_completion_candidates", {}
                )
                active_zone_id = _as_int(active_progress_zone_id)

                if active_zone_id is not None:
                    active_detail = next(
                        (
                            item
                            for item in zone_details
                            if _as_int(item.get("id")) == active_zone_id
                        ),
                        None,
                    )
                    if active_detail is not None:
                        zone_key = str(active_zone_id)
                        zone_record = dict(
                            self._zone_history.get(zone_key) or {}
                        )
                        target_ids = _unique_ints(target_zone_ids)
                        single_zone_task = bool(
                            len(target_ids) == 1
                            and target_ids[0] == active_zone_id
                        )
                        physical_id = _as_int(physical_zone_id)

                        # Private-cloud coverage is usable as a fallback only when
                        # its start timestamp proves the row belongs to this cycle.
                        vendor_progress = _as_int(
                            active_detail.get("vendor_percentage")
                            if active_detail.get("vendor_percentage") is not None
                            else active_detail.get("percentage")
                        )
                        vendor_start = _as_int(
                            active_detail.get("vendor_start_time")
                        )
                        vendor_end = _as_int(active_detail.get("vendor_end_time"))
                        cloud_fresh, cloud_age = fresh_age(coverage_source_age)
                        cycle_start_ms = _as_int(active.get("started_at_ms")) or now_ms
                        for boundary in active.get("zone_cycle_boundaries") or []:
                            if not isinstance(boundary, dict):
                                continue
                            if _as_int(boundary.get("zone_id")) != active_zone_id:
                                continue
                            boundary_ms = _as_int(boundary.get("at_ms"))
                            if boundary_ms is not None:
                                cycle_start_ms = max(cycle_start_ms, boundary_ms)
                        vendor_start_ms = (
                            _timestamp_ms(vendor_start) if vendor_start else None
                        )
                        vendor_end_ms = _timestamp_ms(vendor_end) if vendor_end else None
                        cloud_current_cycle = bool(
                            cloud_fresh
                            and vendor_progress is not None
                            and vendor_start_ms is not None
                            and vendor_start_ms
                            >= cycle_start_ms - _COMPLETION_CYCLE_START_TOLERANCE_MS
                            and vendor_start_ms
                            <= now_ms + _COMPLETION_CYCLE_START_TOLERANCE_MS
                        )
                        cloud_end_current_cycle = bool(
                            cloud_current_cycle
                            and vendor_end_ms is not None
                            and vendor_end_ms
                            >= cycle_start_ms - _COMPLETION_CYCLE_START_TOLERANCE_MS
                            and vendor_end_ms
                            <= now_ms + _COMPLETION_CYCLE_START_TOLERANCE_MS
                            and (vendor_progress or 0)
                            >= VENDOR_COMPLETION_PROGRESS_MIN
                        )

                        source = str(active_zone_progress_source or "")
                        source_fresh, source_age = fresh_age(
                            active_zone_progress_source_age
                        )
                        progress = _as_int(active_zone_progress)
                        route_zone_matches = not (
                            source == "mqtt_route_progress"
                            and physical_id is not None
                            and physical_id != active_zone_id
                        )

                        task_source = str(task_progress_source or "")
                        task_fresh, task_age = fresh_age(task_progress_source_age)
                        task_value = _as_int(task_progress)
                        task_evidence_fresh = bool(
                            single_zone_task
                            and task_source in _SINGLE_ZONE_TASK_COMPLETION_SOURCES
                            and task_fresh
                            and task_value is not None
                        )

                        evidence_progress = None
                        evidence_source = None
                        evidence_age = None
                        if (
                            source in _ACTIVE_ZONE_COMPLETION_SOURCES
                            and source_fresh
                            and progress is not None
                            and route_zone_matches
                        ):
                            evidence_progress = progress
                            evidence_source = source
                            evidence_age = source_age
                        elif task_evidence_fresh:
                            evidence_progress = task_value
                            evidence_source = f"{task_source}_single_zone"
                            evidence_age = task_age
                        elif cloud_current_cycle:
                            evidence_progress = vendor_progress
                            evidence_source = "private_zone_coverage"
                            evidence_age = cloud_age

                        if evidence_progress is not None and evidence_source:
                            previous_progress = _as_int(progress_state.get(zone_key))
                            same_cycle_persisted = (
                                zone_record.get("last_completed_cycle_id")
                                == active.get("id")
                            )
                            sample_ms = now_ms - int(
                                max(0.0, float(evidence_age or 0.0)) * 1000
                            )
                            evidence_snapshot = {
                                "progress": evidence_progress,
                                "source": evidence_source,
                                "source_age_s": (
                                    round(float(evidence_age), 3)
                                    if evidence_age is not None
                                    else None
                                ),
                                "sample_ms": sample_ms,
                                "observed_at": _iso(now_ms),
                            }

                            if evidence_progress < VENDOR_COMPLETION_PROGRESS_MIN:
                                seen_incomplete.add(active_zone_id)
                                completion_candidates.pop(zone_key, None)
                                # Heal older optimistic in-memory state, but never
                                # retract a beta20 completion already persisted for
                                # this exact cycle because small vendor regressions
                                # around 95% are legitimate.
                                if (
                                    active_zone_id in completion_confirmed
                                    and not same_cycle_persisted
                                ):
                                    completion_confirmed.discard(active_zone_id)
                                if (
                                    previous_progress is not None
                                    and previous_progress
                                    >= VENDOR_COMPLETION_PROGRESS_MIN
                                    and not same_cycle_persisted
                                ):
                                    progress_state[zone_key] = evidence_progress
                                    if active.get("completion_reason") == "vendor_progress":
                                        active["completed"] = None
                                        active["completion_reason"] = None
                                        active["final_progress"] = {}
                                else:
                                    progress_state[zone_key] = max(
                                        previous_progress or 0, evidence_progress
                                    )
                                active["last_completion_rejection"] = None
                            else:
                                progress_state[zone_key] = max(
                                    previous_progress or 0, evidence_progress
                                )
                                activity_name = str(activity or "").lower()
                                finish_signals: list[str] = []
                                if evidence_progress >= 100:
                                    finish_signals.append("progress_100")
                                if (
                                    activity_name == ACTIVITY_RETURNING
                                    and evidence_progress
                                    >= VENDOR_COMPLETION_PROGRESS_MIN
                                ):
                                    finish_signals.append("returning_after_95")
                                if cloud_end_current_cycle:
                                    finish_signals.append("current_cycle_cloud_end")
                                if task_evidence_fresh and (task_value or 0) >= 100:
                                    finish_signals.append("single_zone_task_100")

                                if activity_name == ACTIVITY_ERROR:
                                    active["last_completion_rejection"] = {
                                        **evidence_snapshot,
                                        "zone_id": active_zone_id,
                                        "reason": "error_state",
                                    }
                                elif active_zone_id not in seen_incomplete:
                                    active["last_completion_rejection"] = {
                                        **evidence_snapshot,
                                        "zone_id": active_zone_id,
                                        "reason": "high_before_current_cycle_low",
                                    }
                                elif not finish_signals:
                                    active["last_completion_rejection"] = {
                                        **evidence_snapshot,
                                        "zone_id": active_zone_id,
                                        "reason": "awaiting_finish_signal",
                                    }
                                elif active_zone_id not in completion_confirmed:
                                    previous_evidence = last_evidence.get(zone_key)
                                    previous_evidence_progress = _as_int(
                                        (previous_evidence or {}).get("progress")
                                    )
                                    sudden_jump = bool(
                                        previous_evidence_progress is None
                                        or evidence_progress
                                        - previous_evidence_progress
                                        > _COMPLETION_JUMP_GUARD_PERCENT
                                    )
                                    candidate = completion_candidates.get(zone_key)
                                    candidate_sample = _as_int(
                                        (candidate or {}).get("sample_ms")
                                    )
                                    second_fresh_sample = bool(
                                        candidate_sample is not None
                                        and sample_ms
                                        >= candidate_sample
                                        + _COMPLETION_SAMPLE_ADVANCE_MS
                                    )
                                    corroborated = bool(
                                        cloud_end_current_cycle
                                        or (
                                            task_evidence_fresh
                                            and (task_value or 0) >= 100
                                            and not evidence_source.endswith(
                                                "_single_zone"
                                            )
                                        )
                                    )
                                    if (
                                        candidate is not None
                                        and not second_fresh_sample
                                        and not corroborated
                                    ):
                                        active["last_completion_rejection"] = {
                                            **evidence_snapshot,
                                            "zone_id": active_zone_id,
                                            "reason": "waiting_for_second_fresh_sample",
                                        }
                                    elif sudden_jump and not corroborated and candidate is None:
                                        completion_candidates[zone_key] = {
                                            **evidence_snapshot,
                                            "zone_id": active_zone_id,
                                            "finish_signals": list(finish_signals),
                                        }
                                        active["last_completion_rejection"] = {
                                            **evidence_snapshot,
                                            "zone_id": active_zone_id,
                                            "reason": "guarded_large_progress_jump",
                                        }
                                    else:
                                        confirmation = (
                                            "current_cycle_cloud_end"
                                            if cloud_end_current_cycle
                                            else "single_zone_task_100"
                                            if task_evidence_fresh
                                            and (task_value or 0) >= 100
                                            and not evidence_source.endswith("_single_zone")
                                            else "second_fresh_sample"
                                            if candidate is not None
                                            else finish_signals[0]
                                        )
                                        completion_confirmed.add(active_zone_id)
                                        completion_candidates.pop(zone_key, None)
                                        active["last_completion_rejection"] = None
                                        if not same_cycle_persisted:
                                            zone_record.update(
                                                {
                                                    "id": active_zone_id,
                                                    "name": zone_record.get("name")
                                                    or active_detail.get("name")
                                                    or f"Zone {active_zone_id}",
                                                    "last_completed_at": _iso(now_ms),
                                                    "last_completed_progress": evidence_progress,
                                                    "last_completed_source": evidence_source,
                                                    "last_completed_confirmation": confirmation,
                                                    "last_completed_cycle_id": active.get("id"),
                                                }
                                            )
                                            self._zone_history[zone_key] = zone_record
                                        final_progress = active.setdefault(
                                            "final_progress", {}
                                        )
                                        final_progress[zone_key] = evidence_progress

                            last_evidence[zone_key] = evidence_snapshot

                active["task_zone_seen_incomplete"] = sorted(seen_incomplete)
                active["task_zone_completion_confirmed"] = sorted(
                    completion_confirmed
                )
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
                selected_zone_ids = _unique_ints(active.get("zone_ids"))
                if (
                    selected_zone_ids
                    and set(selected_zone_ids).issubset(completion_confirmed)
                ):
                    active["completed"] = True
                    active["completion_reason"] = (
                        active.get("completion_reason") or "vendor_progress"
                    )
                elif active.get("completion_reason") == "vendor_progress":
                    active["completed"] = None
                    active["completion_reason"] = None
                    active["final_progress"] = {}
                self._update_active_metadata_locked(active)
                changed = True

        if changed:
            self._schedule_index_save()
            self._schedule_active_save()

    def update_from_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.update_zone_history(
            snapshot.get("coverage"),
            snapshot.get("zone_details") or [],
            active_zone_progress=snapshot.get("active_zone_progress"),
            active_progress_zone_id=snapshot.get("active_zone_progress_zone_id"),
            active_zone_progress_source=snapshot.get("active_zone_progress_source"),
            active_zone_progress_source_age=snapshot.get(
                "active_zone_progress_source_age"
            ),
            task_progress=snapshot.get("mowing_progress"),
            task_progress_source=snapshot.get("mowing_progress_source"),
            task_progress_source_age=snapshot.get("mowing_progress_source_age"),
            target_zone_ids=snapshot.get("target_zone_ids") or [],
            physical_zone_id=snapshot.get("current_physical_zone_id"),
            coverage_source_age=snapshot.get("coverage_source_age"),
            activity=snapshot.get("activity"),
            cycle_reset_pending=bool(snapshot.get("cycle_value_reset_pending")),
        )
'''
replace_between(
    history_path,
    '''    def update_zone_history(\n''',
    '''\n    # --------------------------------------------------------------- payload\n''',
    dedent(new_zone_history).lstrip("\n"),
    "zone completion resolver",
)

# ---------------------------------------------------------- zone state attrs
zone_state_path = COMPONENT / "zone_state.py"
replace_once(
    zone_state_path,
    '''                "last_completed_at": (\n                    detail.get("last_completed_at")\n                    or persisted.get("last_completed_at")\n                ),\n                "progress_source": (\n''',
    '''                "last_completed_at": (\n                    detail.get("last_completed_at")\n                    or persisted.get("last_completed_at")\n                ),\n                "last_completed_progress": (\n                    detail.get("last_completed_progress")\n                    if detail.get("last_completed_progress") is not None\n                    else persisted.get("last_completed_progress")\n                ),\n                "last_completed_source": (\n                    detail.get("last_completed_source")\n                    or persisted.get("last_completed_source")\n                ),\n                "last_completed_confirmation": (\n                    detail.get("last_completed_confirmation")\n                    or persisted.get("last_completed_confirmation")\n                ),\n                "last_completed_cycle_id": (\n                    detail.get("last_completed_cycle_id")\n                    or persisted.get("last_completed_cycle_id")\n                ),\n                "progress_source": (\n''',
    "zone completion metadata",
)

# ------------------------------------------------------------- diagnostics
diagnostics_path = COMPONENT / "diagnostics.py"
replace_once(
    diagnostics_path,
    '''                    "active_zone_progress_source",\n                    "active_zone_progress_zone_id",\n                    "session_area",\n''',
    '''                    "active_zone_progress_source",\n                    "active_zone_progress_source_age",\n                    "active_zone_progress_zone_id",\n                    "coverage_source_age",\n                    "session_area",\n''',
    "completion freshness diagnostics",
)
replace_once(
    diagnostics_path,
    '''            "Resume diagnostics record only the explicit command trace already held in memory; downloading diagnostics never sends Resume.",\n''',
    '''            "Resume diagnostics record only the explicit command trace already held in memory; downloading diagnostics never sends Resume.",\n            "Zone Last completed is confirmed from fresh current-cycle telemetry; last-known values cannot complete a zone and docking only finalizes route history.",\n''',
    "completion diagnostics note",
)

# ------------------------------------------------------------- regressions
# beta19 keeps its historical release checks after beta20 takes identity.
beta19_test = ROOT / "tests" / "test_v043_beta19.py"
replace_once(
    beta19_test,
    '''import json\nfrom pathlib import Path\n''',
    '''from pathlib import Path\n''',
    "beta19 unused json import",
)
replace_once(
    beta19_test,
    '''def test_beta19_identity():\n    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))\n    assert manifest["version"] == "0.4.3-beta19"\n    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta19.md").read_text(encoding="utf-8")\n    assert notes.startswith("title: Navimower 0.4.3-beta19")\n''',
    '''def test_beta19_release_notes_remain_available():\n    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta19.md").read_text(encoding="utf-8")\n    assert notes.startswith("title: Navimower 0.4.3-beta19")\n''',
    "beta19 historical identity",
)

history_test = ROOT / "tests" / "test_history_merge.py"
replace_between(
    history_test,
    '''def practical_completion_threshold_test() -> None:\n''',
    '''\n\npractical_completion_threshold_test()\n''',
    r'''def practical_completion_threshold_test() -> None:
    Store.values.clear()
    manager = history.NavimowerHistory(FakeHass(), "entry-threshold", "TEST")
    manager.process_pose(
        position={"x": 0.0, "y": 0.0},
        pose_time=2_300_000_000,
        heading=0.0,
        activity="mowing",
        cutting=True,
        docked=False,
        returning=False,
        zone_ids=[13],
        physical_zone_id=13,
    )
    manager.update_zone_history(
        {"zones": [{"id": 13, "pct": 90}]},
        [{"id": 13, "name": "Street", "percentage": 90}],
        active_zone_progress=90,
        active_progress_zone_id=13,
        active_zone_progress_source="mqtt_map_work_position",
        active_zone_progress_source_age=0,
        target_zone_ids=[13],
        physical_zone_id=13,
        activity="mowing",
        observed_at=2_300_000_010,
    )
    manager.update_zone_history(
        {"zones": [{"id": 13, "pct": 100}]},
        [{"id": 13, "name": "Street", "percentage": 100}],
        active_zone_progress=100,
        active_progress_zone_id=13,
        active_zone_progress_source="mqtt_map_work_position",
        active_zone_progress_source_age=0,
        target_zone_ids=[13],
        physical_zone_id=13,
        activity="mowing",
        observed_at=2_300_000_020,
    )
    active = manager.active_session
    assert active is not None
    assert active["completed"] is True
    assert active["completion_reason"] == "vendor_progress"
    zone = manager.zone_history()["13"]
    assert zone["last_completed_progress"] == 100
    assert zone["last_completed_source"] == "mqtt_map_work_position"
    assert zone["last_completed_confirmation"] == "progress_100"
''',
    "practical completion test",
)

beta20_runtime_tests = r'''
async def live_completion_before_dock_test() -> None:
    Store.values.clear()
    hass = FakeHass()
    manager = history.NavimowerHistory(hass, "entry-live-complete", "TEST")
    manager.process_pose(
        position={"x": 0.0, "y": 0.0}, pose_time=2_400_000_000,
        heading=0.0, activity="mowing", cutting=True, docked=False,
        returning=False, zone_ids=[24], physical_zone_id=24,
    )
    manager.update_zone_history(
        {"zones": [{"id": 24, "pct": 91}]},
        [{"id": 24, "name": "Yard", "progress": 91, "percentage": 91}],
        active_zone_progress=91, active_progress_zone_id=24,
        active_zone_progress_source="mqtt_map_work_position",
        active_zone_progress_source_age=0, target_zone_ids=[24],
        physical_zone_id=24, activity="mowing", observed_at=2_400_000_010,
    )
    manager.update_zone_history(
        {"zones": [{"id": 24, "pct": 100}]},
        [{"id": 24, "name": "Yard", "progress": 100, "percentage": 91}],
        active_zone_progress=100, active_progress_zone_id=24,
        active_zone_progress_source="mqtt_map_work_position",
        active_zone_progress_source_age=0, target_zone_ids=[24],
        physical_zone_id=24, activity="mowing", observed_at=2_400_000_020,
    )
    zone_before_dock = manager.zone_history()["24"]
    completed_at = zone_before_dock["last_completed_at"]
    assert zone_before_dock["last_completed_progress"] == 100
    assert manager.active_session["task_zone_completion_confirmed"] == [24]

    manager.process_pose(
        position={"x": 0.1, "y": 0.1}, pose_time=2_400_000_030,
        heading=0.0, activity="docked", cutting=False, docked=True,
        returning=False, zone_ids=[24], completed=None,
    )
    assert manager.zone_history()["24"]["last_completed_at"] == completed_at
    assert manager._active_id is None
    if hass.tasks:
        await asyncio.gather(*hass.tasks)


asyncio.run(live_completion_before_dock_test())


def guarded_source_jump_test() -> None:
    Store.values.clear()
    manager = history.NavimowerHistory(FakeHass(), "entry-source-jump", "TEST")
    manager.process_pose(
        position={"x": 1.0, "y": 1.0}, pose_time=2_410_000_000,
        heading=0.0, activity="mowing", cutting=True, docked=False,
        returning=False, zone_ids=[24], physical_zone_id=24,
    )
    manager.update_zone_history(
        {"zones": [{"id": 24, "pct": 25}]},
        [{"id": 24, "name": "Yard", "percentage": 25}],
        active_zone_progress=25, active_progress_zone_id=24,
        active_zone_progress_source="mqtt_map_work_position",
        active_zone_progress_source_age=0, target_zone_ids=[24],
        physical_zone_id=24, activity="mowing", observed_at=2_410_000_010,
    )
    # A sudden fresh 100 from another source becomes a candidate, not completion.
    manager.update_zone_history(
        {"zones": [{"id": 24, "pct": 25}]},
        [{"id": 24, "name": "Yard", "percentage": 25}],
        active_zone_progress=100, active_progress_zone_id=24,
        active_zone_progress_source="private_map_work_position",
        active_zone_progress_source_age=0, target_zone_ids=[24],
        physical_zone_id=24, activity="mowing", observed_at=2_410_000_020,
    )
    assert "last_completed_at" not in manager.zone_history()["24"]
    diag = manager.cycle_diagnostics()["active_completion"]
    assert diag["candidates"]["24"]["progress"] == 100

    # Re-reading the same cached private sample must still not complete it.
    manager.update_zone_history(
        {"zones": [{"id": 24, "pct": 25}]},
        [{"id": 24, "name": "Yard", "percentage": 25}],
        active_zone_progress=100, active_progress_zone_id=24,
        active_zone_progress_source="private_map_work_position",
        active_zone_progress_source_age=5, target_zone_ids=[24],
        physical_zone_id=24, activity="mowing", observed_at=2_410_000_025,
    )
    assert "last_completed_at" not in manager.zone_history()["24"]

    # A genuinely newer high sample confirms the guarded candidate.
    manager.update_zone_history(
        {"zones": [{"id": 24, "pct": 25}]},
        [{"id": 24, "name": "Yard", "percentage": 25}],
        active_zone_progress=100, active_progress_zone_id=24,
        active_zone_progress_source="private_map_work_position",
        active_zone_progress_source_age=0, target_zone_ids=[24],
        physical_zone_id=24, activity="mowing", observed_at=2_410_000_030,
    )
    zone = manager.zone_history()["24"]
    assert zone["last_completed_source"] == "private_map_work_position"
    assert zone["last_completed_confirmation"] == "second_fresh_sample"


guarded_source_jump_test()


def stale_last_known_never_completes_test() -> None:
    Store.values.clear()
    manager = history.NavimowerHistory(FakeHass(), "entry-last-known", "TEST")
    manager.process_pose(
        position={"x": 2.0, "y": 2.0}, pose_time=2_420_000_000,
        heading=0.0, activity="mowing", cutting=True, docked=False,
        returning=False, zone_ids=[13], physical_zone_id=13,
    )
    manager.update_zone_history(
        {"zones": [{"id": 13, "pct": 50}]},
        [{"id": 13, "name": "Street", "percentage": 50}],
        active_zone_progress=50, active_progress_zone_id=13,
        active_zone_progress_source="mqtt_map_work_position",
        active_zone_progress_source_age=0, target_zone_ids=[13],
        physical_zone_id=13, activity="mowing", observed_at=2_420_000_010,
    )
    manager.update_zone_history(
        {"zones": [{"id": 13, "pct": 50}]},
        [{"id": 13, "name": "Street", "percentage": 50}],
        active_zone_progress=100, active_progress_zone_id=13,
        active_zone_progress_source="last_known_zone",
        active_zone_progress_source_age=None,
        task_progress=100, task_progress_source="last_known",
        task_progress_source_age=None, target_zone_ids=[13],
        physical_zone_id=13, activity="returning", observed_at=2_420_000_020,
    )
    assert "last_completed_at" not in manager.zone_history()["13"]


stale_last_known_never_completes_test()


def current_cycle_cloud_fallback_test() -> None:
    Store.values.clear()
    manager = history.NavimowerHistory(FakeHass(), "entry-cloud-fallback", "TEST")
    manager.process_pose(
        position={"x": 3.0, "y": 3.0}, pose_time=2_430_000_000,
        heading=0.0, activity="mowing", cutting=True, docked=False,
        returning=False, zone_ids=[13], physical_zone_id=13,
    )
    start = 2_430_000_000
    manager.update_zone_history(
        {"zones": [{"id": 13, "pct": 40, "start_time": start}]},
        [{"id": 13, "name": "Street", "percentage": 40,
          "vendor_start_time": start, "vendor_end_time": None}],
        active_zone_progress=None, active_progress_zone_id=13,
        target_zone_ids=[13], physical_zone_id=13,
        coverage_source_age=0, activity="mowing", observed_at=2_430_000_010,
    )
    manager.update_zone_history(
        {"zones": [{"id": 13, "pct": 97, "start_time": start,
                    "end_time": 2_430_000_020}]},
        [{"id": 13, "name": "Street", "percentage": 97,
          "vendor_start_time": start, "vendor_end_time": 2_430_000_020}],
        active_zone_progress=None, active_progress_zone_id=13,
        target_zone_ids=[13], physical_zone_id=13,
        coverage_source_age=0, activity="returning", observed_at=2_430_000_021,
    )
    zone = manager.zone_history()["13"]
    assert zone["last_completed_progress"] == 97
    assert zone["last_completed_source"] == "private_zone_coverage"
    assert zone["last_completed_confirmation"] == "current_cycle_cloud_end"


current_cycle_cloud_fallback_test()
print("beta20 completion arbitration tests passed")
'''
replace_between(
    history_test,
    '''async def dock_completion_history_test() -> None:\n''',
    '''\n\nasync def explicit_partial_reset_test() -> None:\n''',
    dedent(beta20_runtime_tests).lstrip("\n") + "\n\nasync def explicit_partial_reset_test() -> None:\n",
    "beta20 runtime completion tests",
)

write(ROOT / "tests" / "test_v043_beta20.py", r'''
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta20_identity():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta20"
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
    assert "last_completed_progress" not in dock
    assert "completed=self._session_completed(snapshot) if docked else None" not in coordinator
    assert "completed=None" in coordinator


def test_completion_is_fresh_current_cycle_and_fail_closed():
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    assert "_COMPLETION_EVIDENCE_MAX_AGE_SECONDS = 30.0" in history
    assert '"last_known_zone"' not in history[history.index("_ACTIVE_ZONE_COMPLETION_SOURCES"):history.index("SESSION_DETAIL_POINT_FORMAT")]
    assert '"private_zone_coverage"' in history
    assert '"guarded_large_progress_jump"' in history
    assert '"waiting_for_second_fresh_sample"' in history
    assert '"last_completed_source"' in history
    assert '"last_completed_confirmation"' in history
    assert 'self._private_endpoint_age("path_info_time")' in coordinator


def test_completion_diagnostics_expose_source_and_freshness():
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    zone_state = (COMPONENT / "zone_state.py").read_text(encoding="utf-8")
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    assert '"active_zone_progress_source_age"' in diagnostics
    assert '"coverage_source_age"' in diagnostics
    assert '"active_completion"' in history
    assert '"last_completed_source"' in zone_state
    assert '"last_completed_confirmation"' in zone_state
''')

# ------------------------------------------------------------- release notes
changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
marker = "# Changelog\n\n\n"
if not changelog.startswith(marker):
    raise SystemExit("Unexpected changelog header")
section = '''## 0.4.3-beta20\n\nFresh current-cycle per-zone completion arbitration.\n\n### Fixed\n\n- Advance a zone's `last_completed` when the active zone is actually confirmed complete; docking is no longer the completion trigger.\n- Keep Navimower Schedule one-zone-at-a-time behavior: a confirmed `last_completed` can release the active zone and let the scheduler choose the next eligible zone without a dock round-trip.\n- Reject stale `last_known` progress as completion evidence and guard large cross-source jumps until a second fresh sample or corroborating current-cycle signal arrives.\n\n### Fallbacks\n\n- Prefer fresh active-zone MQTT progress, then fresh private-cloud work progress, with the single-zone task percentage available only when exactly one target zone is active.\n- Accept private-cloud per-zone coverage as a fallback only when its `startTime` belongs to the current cycle; `endTime + >=95%` is useful only with that current-cycle proof.\n- A fresh returning transition at >=95% or a trusted 100% progress counter can finish the zone without waiting for Docked.\n\n### Diagnostics\n\n- Persist `last_completed_source`, confirmation reason, progress and cycle ID with each confirmed zone completion.\n- Expose completion candidates/rejections plus active-zone and coverage source ages in diagnostics.\n\n\n'''
changelog_path.write_text(marker + section + changelog[len(marker):], encoding="utf-8")

write(ROOT / ".github" / "release-notes" / "0.4.3-beta20.md", r'''
title: Navimower 0.4.3-beta20

Fresh current-cycle per-zone completion arbitration.

### Fixed
- Zone **Last completed** now advances from confirmed current-cycle telemetry instead of waiting for the mower to dock.
- **Docked** only finalizes the route/session history; it no longer creates a zone completion timestamp.
- **Navimower Schedule** remains one-zone-at-a-time and can move on when that active zone's confirmed `last_completed` advances.

### Source arbitration
- Fresh active-zone MQTT work/route progress is preferred, with fresh private-cloud work progress as fallback.
- For a task with exactly one target zone, the fresh whole-task percentage is allowed as an additional fallback/corroborating counter; it is never assigned to a zone in multi-zone vendor tasks.
- `last_known` values may keep public telemetry stable but can never create a new completion event.
- Completion evidence older than **30 seconds** is rejected.
- A large progress jump is held as a candidate until a second genuinely fresh sample or a corroborating finish signal arrives.

### Cloud fallback
- Private-cloud per-zone coverage is completion evidence only when its `startTime` proves that the row belongs to the active cycle.
- A current-cycle `endTime` with at least the existing **95%** practical threshold can corroborate completion; stale previous-cycle end timestamps remain non-authoritative.
- A fresh returning transition at >=95% or a trusted 100% progress counter can confirm completion without a dock round-trip.

### Diagnostics
- Confirmed zone history now records completion source, confirmation reason, progress and cycle ID.
- Diagnostics include completion candidates/rejections plus active-zone and coverage source ages for field verification.
''')
