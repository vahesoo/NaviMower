from pathlib import Path

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


beta3_test = ROOT / "tests" / "test_v034_beta3.py"
replace_once(
    beta3_test,
    '''def test_history_heals_stale_completion_and_false_completed_flag() -> None:\n    source = (COMPONENT / "history.py").read_text()\n    assert "stale_completed_value = bool(" in source\n    assert "vendor_progress < VENDOR_COMPLETION_PROGRESS_MIN" in source\n    assert 'task_progress[str(active_zone_id)] = progress' in source\n    assert 'active.get("completion_reason") == "vendor_progress"' in source\n    assert 'active["completed"] = None' in source\n''',
    '''def test_history_heals_stale_completion_and_false_completed_flag() -> None:\n    source = (COMPONENT / "history.py").read_text()\n    assert '"task_zone_last_evidence"' in source\n    assert '"task_zone_completion_candidates"' in source\n    assert "previous_progress" in source\n    assert 'active.get("completion_reason") == "vendor_progress"' in source\n    assert 'active["completed"] = None' in source\n''',
    "beta3 completion regression markers",
)

location_path = COMPONENT / "location.py"
replace_once(
    location_path,
    '''    loc["_progress_updated"] = False\n    loc["_area_updated"] = False\n''',
    '''    loc["_progress_updated"] = False\n    loc["_route_progress_updated"] = False\n    loc["_work_progress_updated"] = False\n    loc["_task_progress_updated"] = False\n    loc["_area_updated"] = False\n''',
    "mqtt per-counter flags",
)
replace_once(
    location_path,
    '''            if "currentMowProgress" in item:\n                loc["mow_progress"] = item.get("currentMowProgress")\n                loc["_progress_updated"] = True\n''',
    '''            if "currentMowProgress" in item:\n                loc["mow_progress"] = item.get("currentMowProgress")\n                loc["_progress_updated"] = True\n                loc["_route_progress_updated"] = True\n''',
    "mqtt route freshness flag",
)
replace_once(
    location_path,
    '''                    loc["work_progress"] = decoded["progress"]\n                    loc["_progress_updated"] = True\n''',
    '''                    loc["work_progress"] = decoded["progress"]\n                    loc["_progress_updated"] = True\n                    loc["_work_progress_updated"] = True\n''',
    "mqtt work freshness flag",
)
replace_once(
    location_path,
    '''            if "mowingPercentage" in item:\n                loc["mowing_percentage"] = item.get("mowingPercentage")\n                loc["_progress_updated"] = True\n''',
    '''            if "mowingPercentage" in item:\n                loc["mowing_percentage"] = item.get("mowingPercentage")\n                loc["_progress_updated"] = True\n                loc["_task_progress_updated"] = True\n''',
    "mqtt task freshness flag",
)

coordinator_path = COMPONENT / "coordinator.py"
replace_once(
    coordinator_path,
    '''        self._mqtt_progress_last_update: float | None = None\n        self._mqtt_area_last_update: float | None = None\n''',
    '''        self._mqtt_progress_last_update: float | None = None\n        self._mqtt_route_progress_last_update: float | None = None\n        self._mqtt_work_progress_last_update: float | None = None\n        self._mqtt_task_progress_last_update: float | None = None\n        self._mqtt_area_last_update: float | None = None\n''',
    "mqtt per-counter timestamps",
)
replace_once(
    coordinator_path,
    '''        packed_work = decode_map_work_position(\n            _find(location, "map_work_position", "mapWorkPosition")\n            or _find(index2, "map_work_position", "mapWorkPosition")\n        )\n''',
    '''        location_work_word = _find(\n            location, "map_work_position", "mapWorkPosition"\n        )\n        index2_work_word = _find(\n            index2, "map_work_position", "mapWorkPosition"\n        )\n        packed_work_endpoint = (\n            "location"\n            if location_work_word\n            else "index2"\n            if index2_work_word\n            else None\n        )\n        packed_work = decode_map_work_position(\n            location_work_word or index2_work_word\n        )\n''',
    "private work endpoint identity",
)
replace_once(
    coordinator_path,
    '''            "work_progress": work_progress,\n            "mow_route_progress": mqtt_route_progress,\n''',
    '''            "work_progress": work_progress,\n            "work_progress_source_age": (\n                self._private_endpoint_age(packed_work_endpoint)\n                if packed_work_endpoint is not None\n                else None\n            ),\n            "mow_route_progress": mqtt_route_progress,\n''',
    "private work freshness",
)
replace_between(
    coordinator_path,
    '''    def _fresh_mqtt_progress_values(self) -> dict[str, int | None]:\n''',
    '''\n    def _mark_display_cycle_reset(\n''',
    '''    def _fresh_mqtt_progress_values(self) -> dict[str, int | None]:\n        mqtt = self._mqtt_location or {}\n\n        def fresh(key: str, updated_at: float | None) -> int | None:\n            age = self._age_since(updated_at)\n            if age is None or age > MQTT_TELEMETRY_STALE_SECONDS:\n                return None\n            return _progress_percent(mqtt.get(key))\n\n        return {\n            "mowing_percentage": fresh(\n                "mowing_percentage", self._mqtt_task_progress_last_update\n            ),\n            "work_progress": fresh(\n                "work_progress", self._mqtt_work_progress_last_update\n            ),\n            "route_progress": fresh(\n                "mow_progress", self._mqtt_route_progress_last_update\n            ),\n        }\n''',
    "mqtt per-counter resolver",
)
replace_once(
    coordinator_path,
    '''            self._age_since(self._mqtt_progress_last_update)\n            if progress_source == "mqtt_task_percentage"\n''',
    '''            self._age_since(self._mqtt_task_progress_last_update)\n            if progress_source == "mqtt_task_percentage"\n''',
    "mqtt task source age",
)
replace_once(
    coordinator_path,
    '''        snapshot["active_zone_progress_source_age"] = (\n            self._age_since(self._mqtt_progress_last_update)\n            if str(active_zone_progress_source or "").startswith("mqtt_")\n            else self._private_endpoint_age("location", "index2")\n            if active_zone_progress_source == "private_map_work_position"\n            else None\n        )\n''',
    '''        snapshot["active_zone_progress_source_age"] = (\n            self._age_since(self._mqtt_work_progress_last_update)\n            if active_zone_progress_source == "mqtt_map_work_position"\n            else self._age_since(self._mqtt_route_progress_last_update)\n            if active_zone_progress_source == "mqtt_route_progress"\n            else _as_float(snapshot.get("work_progress_source_age"))\n            if active_zone_progress_source == "private_map_work_position"\n            else None\n        )\n''',
    "active-zone source-specific age",
)
replace_once(
    coordinator_path,
    '''        merged = dict(self._mqtt_location or {})\n        merged.update(location)\n        self._mqtt_location = merged\n''',
    '''        previous_mqtt = dict(self._mqtt_location or {})\n        merged = dict(previous_mqtt)\n        merged.update(location)\n        self._mqtt_location = merged\n''',
    "mqtt previous counter snapshot",
)
replace_once(
    coordinator_path,
    '''        if bool(location.get("_progress_updated")) or (\n            self._mqtt_progress_last_update is None\n            and any(\n                location.get(key) is not None\n                for key in ("mow_progress", "work_progress", "mowing_percentage")\n            )\n        ):\n            self._mqtt_progress_last_update = now_monotonic\n        if bool(location.get("_area_updated")) or (\n''',
    '''        if bool(location.get("_progress_updated")) or (\n            self._mqtt_progress_last_update is None\n            and any(\n                location.get(key) is not None\n                for key in ("mow_progress", "work_progress", "mowing_percentage")\n            )\n        ):\n            self._mqtt_progress_last_update = now_monotonic\n\n        def counter_updated(flag: str, key: str, stamp: float | None) -> bool:\n            return bool(location.get(flag)) or bool(\n                key in location\n                and location.get(key) is not None\n                and (stamp is None or location.get(key) != previous_mqtt.get(key))\n            )\n\n        if counter_updated(\n            "_route_progress_updated",\n            "mow_progress",\n            self._mqtt_route_progress_last_update,\n        ):\n            self._mqtt_route_progress_last_update = now_monotonic\n        if counter_updated(\n            "_work_progress_updated",\n            "work_progress",\n            self._mqtt_work_progress_last_update,\n        ):\n            self._mqtt_work_progress_last_update = now_monotonic\n        if counter_updated(\n            "_task_progress_updated",\n            "mowing_percentage",\n            self._mqtt_task_progress_last_update,\n        ):\n            self._mqtt_task_progress_last_update = now_monotonic\n        if bool(location.get("_area_updated")) or (\n''',
    "mqtt counter timestamps ingest",
)

mqtt_test = ROOT / "tests" / "test_mqtt_telemetry.py"
replace_once(
    mqtt_test,
    '''assert first["_progress_updated"] is True\nassert first["_area_updated"] is True\nassert first["mow_progress"] == 8400\n''',
    '''assert first["_progress_updated"] is True\nassert first["_route_progress_updated"] is True\nassert first["_work_progress_updated"] is False\nassert first["_task_progress_updated"] is False\nassert first["_area_updated"] is True\nassert first["mow_progress"] == 8400\n''',
    "mqtt route freshness regression",
)
replace_once(
    mqtt_test,
    '''assert second["_progress_updated"] is False\nassert second["_area_updated"] is False\n''',
    '''assert second["_progress_updated"] is False\nassert second["_route_progress_updated"] is False\nassert second["_work_progress_updated"] is False\nassert second["_task_progress_updated"] is False\nassert second["_area_updated"] is False\n''',
    "mqtt cached counter freshness regression",
)

beta20_test = ROOT / "tests" / "test_v043_beta20.py"
replace_once(
    beta20_test,
    '''    assert 'self._private_endpoint_age("path_info_time")' in coordinator\n''',
    '''    assert 'self._private_endpoint_age("path_info_time")' in coordinator\n    assert "_mqtt_route_progress_last_update" in coordinator\n    assert "_mqtt_work_progress_last_update" in coordinator\n    assert "_mqtt_task_progress_last_update" in coordinator\n    assert 'snapshot.get("work_progress_source_age")' in coordinator\n''',
    "beta20 per-source freshness markers",
)

release_notes = ROOT / ".github" / "release-notes" / "0.4.3-beta20.md"
replace_once(
    release_notes,
    '''- Completion evidence older than **30 seconds** is rejected.\n''',
    '''- Completion evidence older than **30 seconds** is rejected, and MQTT route/work/task counters keep independent freshness timestamps so one live counter cannot refresh another cached counter.\n''',
    "release note per-counter freshness",
)
