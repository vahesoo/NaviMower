"""Release regressions for Navimower 0.5.0-beta21."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _source(name: str) -> str:
    text = (COMPONENT / name).read_text(encoding="utf-8")
    if name.endswith(".py"):
        ast.parse(text)
    return text


def test_beta21_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta21"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta21.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Completed on %@",
        "Mowing motor undervoltage protection",
        "five-minute event",
        "Group-aware Mark as read",
        "navimower.export_raw_data",
        "navimower.probe_iot_file",
        "Still pending before stable",
        "No Map Card update is required",
    ):
        assert marker in notes


def test_beta21_groups_vendor_duplicates_before_frontend_publication() -> None:
    feed = _source("notification_feed.py")
    assert "_VENDOR_REPEAT_WINDOW_SECONDS = 5 * 60" in feed
    assert '"1003": "Mowing motor undervoltage protection"' in feed
    assert "def _canonical_vendor_title" in feed
    assert "_UNRESOLVED_VENDOR_TITLE_RE.search(text)" in feed
    assert "def _group_vendor_messages" in feed
    assert 'row["repeat_count"] = max(1, len(ids) or len(reads))' in feed
    assert "grouped_vendor_messages, group_members = _group_vendor_messages" in feed
    assert "coordinator._notification_group_members" in feed
    assert "vendor_raw_count" in feed


def test_beta21_grouped_mark_read_stays_backend_owned() -> None:
    actions = _source("notification_actions.py")
    assert "def _async_vendor_calls_and_refresh" in actions
    assert 'group_map = getattr(coordinator, "_notification_group_members", None)' in actions
    assert '"grouped_count": len(member_ids)' in actions
    assert "for member_id in member_ids" in actions
    assert "await coordinator.async_request_refresh()" in actions


def test_beta21_removes_public_development_probe_surfaces() -> None:
    services = _source("services.py")
    services_yaml = _source("services.yaml")
    assert not (COMPONENT / "raw_export.py").exists()
    assert not (COMPONENT / "iot_file_probe.py").exists()
    for marker in (
        "SERVICE_EXPORT_RAW_DATA",
        "async_export_raw_data",
        "SERVICE_PROBE_IOT_FILE",
        "async_probe_iot_file",
    ):
        assert marker not in services
    assert "export_raw_data:" not in services_yaml
    assert "probe_iot_file:" not in services_yaml


def test_beta21_action_audit_keeps_stable_compatibility_surface() -> None:
    services_yaml = _source("services.yaml")
    for action in (
        "set_schedule:",
        "mow:",
        "continue_last_ordered_run:",
        "continue_task:",
        "resume:",
        "set_gate_area:",
        "delete_gate_area:",
        "mark_notification_read:",
        "mark_all_notifications_read:",
        "set_schedule_queue:",
        "reset_schedule:",
        "relearn_georeference:",
        "refresh_map_snapshot:",
    ):
        assert action in services_yaml
