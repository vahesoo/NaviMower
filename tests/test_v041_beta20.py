"""Regression contracts carried forward from Navimower 0.4.1-beta20."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta20_release_notes_remain_present() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    version = manifest["version"]
    assert version.startswith("0.4.1-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 20
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta20.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta20")
    for phrase in (
        "404",
        "account-level",
        "message/push",
        "commonUserVehicleIndex",
    ):
        assert phrase in notes


def test_beta20_probe_focuses_on_message_push_and_summarizes_404() -> None:
    source = (COMPONENT / "event_probe.py").read_text()
    ast.parse(source)
    assert '"/vehicle/vehicle/event-list"' not in source
    assert source.count('"/message/') >= 15
    assert source.count('"/push/') >= 4
    assert '"not_found_count"' in source
    assert '"not_found_path_count"' in source
    assert 'str(err.code) == "404"' in source
    assert "continue" in source


def test_beta20_probe_has_account_and_device_profiles() -> None:
    source = (COMPONENT / "event_probe.py").read_text()
    for profile in (
        '"account_minimal"',
        '"account_paged"',
        '"device_minimal"',
        '"device_extended"',
    ):
        assert profile in source
    for key in (
        '"msgType"',
        '"noticeType"',
        '"notificationType"',
        '"messageCategory"',
        '"bizType"',
        '"businessType"',
        '"sourceType"',
        '"tabType"',
        '"cursor"',
        '"lastId"',
        '"startTime"',
        '"endTime"',
        '"commonUserVehicleIndex"',
        '"vehicleShareType"',
    ):
        assert key in source


def test_beta20_probe_remains_read_only_and_out_of_coordinator_polling() -> None:
    coordinator = (COMPONENT / "coordinator.py").read_text()
    source = (COMPONENT / "event_probe.py").read_text()
    assert "probe_event_endpoints" not in coordinator
    for forbidden in (
        "/vehicle/set/send",
        "/vehicle/set/save-set-data",
        "/map/index/save",
        "save_setting(",
        "mow_zones(",
    ):
        assert forbidden not in source
