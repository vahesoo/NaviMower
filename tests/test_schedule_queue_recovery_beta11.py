from __future__ import annotations

import ast
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "navimower" / "schedule_queue_recovery_semantics.py"
RUNTIME = ROOT / "custom_components" / "navimower" / "runtime.py"
MANIFEST = ROOT / "custom_components" / "navimower" / "manifest.json"


def _source() -> str:
    return MODULE.read_text(encoding="utf-8")


def _parse_iso(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def _load_recovery_functions(names: set[str]) -> dict[str, Any]:
    source = _source()
    tree = ast.parse(source)
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, Any] = {
        "Any": Any,
        "timedelta": timedelta,
        "parse_iso": _parse_iso,
        "_ACTIVITY_CLOCK_SKEW_SECONDS": 90.0,
    }
    exec(compile(module, MODULE.name, "exec"), namespace)
    return namespace


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


def test_beta18_real_weather_resume_delay_is_recoverable_but_bounded() -> None:
    namespace = _load_recovery_functions(
        {"_as_float", "_fresh_start_after_block", "_confirmed_completion_after_block"}
    )
    fresh_start = namespace["_fresh_start_after_block"]
    completion = namespace["_confirmed_completion_after_block"]

    runtime = {"last_command_at": "2026-09-21T08:28:24.163892+00:00"}
    row = {
        "last_started_at": "2026-09-21T08:27:41.955000+00:00",
        "last_completed_at": "2026-09-21T09:04:37+00:00",
        "vendor_coverage_pct": 100.0,
        "last_completed_progress": 100,
        "cycle_id": "1789979118763-281",
        "last_completed_cycle_id": "1789979118763-281",
        "last_completed_source": "private_zone_coverage",
        "last_completed_confirmation": "coverage_100_after_incomplete",
    }
    assert fresh_start(runtime, row) == row["last_started_at"]
    assert completion(runtime, row) == row["last_completed_at"]

    too_old = dict(row)
    too_old["last_started_at"] = "2026-09-21T08:26:53+00:00"
    assert fresh_start(runtime, too_old) is None
    assert completion(runtime, too_old) is None


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


def test_beta11_recovery_remains_in_the_045_release_line() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"].split("-", 1)[0] == "0.4.5"
