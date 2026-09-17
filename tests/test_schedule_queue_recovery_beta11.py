from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "navimower" / "schedule_queue_recovery_semantics.py"
RUNTIME = ROOT / "custom_components" / "navimower" / "runtime.py"
MANIFEST = ROOT / "custom_components" / "navimower" / "manifest.json"


def _source() -> str:
    return MODULE.read_text(encoding="utf-8")


def test_beta11_recovery_is_fail_closed_and_custom_queue_only() -> None:
    source = _source()
    assert 'runtime.get("suspended_reason") != _BLOCK_REASON' in source
    assert 'controller._order_mode != SCHEDULE_ORDER_CUSTOM' in source
    assert 'unfinished = sorted(started - completed)' in source


def test_beta11_same_zone_auto_resume_requires_live_vendor_mowing() -> None:
    source = _source()
    assert 'if not controller._vendor_mowing(data):' in source
    assert 'observed_zone == zone_id' in source
    assert '"same_zone_auto_resume_recovered"' in source


def test_beta11_completion_recovery_requires_fresh_confirmed_100_percent() -> None:
    source = _source()
    assert 'completed <= started or completed <= blocked' in source
    assert 'coverage < 99.5' in source
    assert 'last_completed_cycle_id' in source
    assert '"same_zone_completion_recovered"' in source


def test_beta11_completion_marks_exact_started_slot_complete() -> None:
    source = _source()
    assert 'slots.add(slot)' in source
    assert 'runtime["completed_queue_slots"] = sorted(slots)' in source
    assert 'completed_zones.add(zone_id)' in source
    assert 'runtime["active_queue_slot"] = None' in source


def test_beta11_never_issues_a_reset_or_mow_command() -> None:
    source = _source()
    assert "_async_send_mow" not in source
    assert "reset=True" not in source
    assert "navimower.mow" not in source


def test_beta11_installs_after_dispatch_weather_semantics() -> None:
    runtime = RUNTIME.read_text(encoding="utf-8")
    weather = runtime.index("install_schedule_dispatch_weather_semantics()")
    recovery = runtime.index("install_schedule_queue_recovery_semantics()")
    assert weather < recovery


def test_beta11_version_is_prepared() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.5-beta11"
