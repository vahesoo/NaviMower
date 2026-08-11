"""Regression contracts for Navimower 0.4.2-beta4."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _source(name: str) -> str:
    return (COMPONENT / name).read_text()


def test_beta4_manifest_release_notes_and_changelog() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"].startswith("0.4.2")

    notes = (ROOT / ".github" / "release-notes" / "0.4.2-beta4.md").read_text()
    assert notes.startswith("title: Navimower 0.4.2-beta4")
    for phrase in (
        "10 vendor",
        "20 Navimower",
        "navimower:",
        "Scheduled mowing started",
        "Mowing paused for night",
        "sunrise",
        "External mowing task",
        "mowStartType",
        "taskDelay",
        "confirmed vendor state",
    ):
        assert phrase in notes

    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert changelog.startswith("# Changelog\n")
    assert "## 0.4.2-beta4" in changelog
    assert "notification center" in changelog.lower()


def test_beta4_notification_center_limits_and_persistence_contract() -> None:
    source = _source("notification_center.py")
    ast.parse(source)

    assert 'LOCAL_NOTIFICATION_PREFIX = "navimower:"' in source
    assert "LOCAL_NOTIFICATION_LIMIT = 20" in source
    assert "VENDOR_NOTIFICATION_LIMIT = 10" in source
    assert "MERGED_NOTIFICATION_LIMIT = LOCAL_NOTIFICATION_LIMIT + VENDOR_NOTIFICATION_LIMIT" in source
    assert 'key = f"{DOMAIN}_notifications_{entry_id}"' in source
    assert "await self._store.async_load()" in source
    assert "await self._store.async_save(payload)" in source
    assert "del self._messages[LOCAL_NOTIFICATION_LIMIT:]" in source
    assert "merged[:MERGED_NOTIFICATION_LIMIT]" in source


def test_beta4_latest_notification_merges_bounded_vendor_and_local_rows() -> None:
    source = _source("beta26_runtime.py")
    ast.parse(source)

    assert "normalized[\"list\"][:VENDOR_NOTIFICATION_LIMIT]" in source
    assert "local_messages = center.messages" in source
    assert "messages = merge_notification_lists(vendor_messages, local_messages)" in source
    assert '"notification_vendor_count": len(vendor_messages)' in source
    assert '"notification_local_count": len(local_messages)' in source
    assert '"notification_count": len(messages)' in source
    assert '"vendor_count": data.get("notification_vendor_count")' in source
    assert '"local_count": data.get("notification_local_count")' in source
    assert '"origin": "vendor"' in source
    assert "refresh_notification_snapshot" in source


def test_beta4_local_events_wait_for_confirmed_vendor_state() -> None:
    source = _source("notification_center.py")
    ast.parse(source)

    assert "def _confirmed_activity" in source
    assert "mqtt_vehicle_state" in source
    assert "MQTT_STATE_MOWING" in source
    assert "MQTT_STATE_RETURNING" in source
    assert "MQTT_DOCKED_STATES" in source
    assert "VEHICLE_STATE_TO_ACTIVITY" in source
    assert "Unknown/transient vendor states do not create synthetic transitions" in source

    # The transition listener must classify the confirmed private/MQTT state,
    # not the coordinator's optimistic pending activity published immediately
    # after a Home Assistant command.
    listener = source[source.index("def _handle_update"):source.index("async def _async_process_transition")]
    assert "self._confirmed_activity(snapshot)" in listener
    assert 'snapshot.get("activity")' not in listener


def test_beta4_known_mowing_attribution_events_are_present() -> None:
    source = _source("notification_center.py")
    ast.parse(source)

    expected = {
        "NM1001": "Scheduled mowing started",
        "NM1002": "Mowing task started",
        "NM1003": "External mowing task started",
        "NM1004": "Mowing paused for night",
        "NM1005": "Unfinished mowing resumed after sunrise",
        "NM1006": "Mowing resumed",
        "NM1007": "Mowing paused for charging",
        "NM1008": "Mowing resumed after charging",
        "NM1009": "Mowing task completed",
        "NM1010": "Scheduled mowing window ended",
        "NM1011": "Mower sent to dock",
    }
    for code, title in expected.items():
        assert f'"{code}"' in source
        assert f'"{title}"' in source

    assert '"origin": "navimower"' in source
    assert '"vendor_code": None' in source
    assert '"error_code": None' in source
    assert '"read": False' in source


def test_beta4_schedule_night_sunrise_and_charging_context_is_conservative() -> None:
    source = _source("notification_center.py")
    ast.parse(source)

    assert "get_astral_event_date" in source
    assert "SUN_EVENT_SUNSET" in source
    assert "SUN_EVENT_SUNRISE" in source
    assert 'settings.get("schedule_enabled") is False' in source
    assert 'get("night_mow") is not False' in source
    assert 'get("return_battery_level")' in source
    assert 'self._interrupted_reason == "night"' in source
    assert 'self._interrupted_reason == "charging"' in source
    assert "inferred_from_sunset_and_night_mowing_off" in source
    assert "inferred_from_return_battery_threshold" in source
    assert "inferred_from_retained_task_and_sunrise" in source

    # Do not turn the historical practical-completion threshold into a local
    # user-facing completion claim; beta4 requires an unambiguous 100% value.
    assert "progress >= 100" in source
    assert "VENDOR_COMPLETION_PROGRESS_MIN" not in source


def test_beta4_ha_mow_and_external_zone_attribution_stay_distinct() -> None:
    source = _source("notification_center.py")
    location = _source("location.py")
    ast.parse(source)
    ast.parse(location)

    assert 'mow_trace.get("requested_zone_names")' in source
    assert 'mow_trace.get("resolved_zone_names")' in source
    assert 'ordered = bool(mow_trace.get("ordered"))' in source
    assert "_ordered_zone_phrase(names)" in source
    assert "Zone order is selected by the mower" in source

    assert 'self._mqtt_value("partition_ids")' in source
    assert 'self._mqtt_value("mow_start_type")' in source
    assert 'self._mqtt_value("task_delay")' in source
    assert "does not assume it came from the mobile app" in source
    assert 'loc["mow_start_type"] = item.get("mowStartType")' in location
    assert 'loc["task_delay"] = item.get("taskDelay")' in location


def test_beta4_notification_read_actions_dispatch_local_ids_without_vendor_calls() -> None:
    source = _source("notification_actions.py")
    ast.parse(source)

    assert "LOCAL_NOTIFICATION_PREFIX" in source
    assert "message_id.startswith(LOCAL_NOTIFICATION_PREFIX)" in source
    assert "await center.async_mark_read(message_id)" in source
    assert "await center.async_mark_all_read()" in source
    assert "_NOTIFICATION_DETAIL_PATH" in source
    assert "_NOTIFICATION_MARK_ALL_PATH" in source
    assert "Local rows" not in source or "vendor" in source


def test_beta4_notification_center_lifecycle_and_diagnostics_are_wired() -> None:
    init = _source("__init__.py")
    diagnostics = _source("diagnostics.py")
    mower = _source("lawn_mower.py")
    ast.parse(init)
    ast.parse(diagnostics)
    ast.parse(mower)

    assert "NavimowerNotificationCenter(" in init
    assert "await notification_center.async_load()" in init
    assert "notification_center.start()" in init
    assert "await notification_center.async_stop()" in init
    assert "NavimowerNotificationCenter.async_remove_all" in init

    assert '"notification_center": sanitize' in diagnostics
    assert '"vendor_count": data.get("notification_vendor_count")' in diagnostics
    assert '"local_count": data.get("notification_local_count")' in diagnostics
    assert '"origin": data.get("notification_origin")' in diagnostics

    assert 'center.note_dock_command("lawn_mower.dock")' in mower
    assert "center.clear_dock_command()" in mower
