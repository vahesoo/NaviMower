"""Release regressions for Navimower 0.5.0-beta11."""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
COORDINATOR = COMPONENT / "coordinator.py"
SCHEDULE = COMPONENT / "schedule_v2_semantics.py"
SCHEDULE_LOGIC = COMPONENT / "schedule_logic.py"
CONFIG_FLOW = COMPONENT / "config_flow_base.py"


def _load_schedule_logic():
    spec = importlib.util.spec_from_file_location("beta11_schedule_logic", SCHEDULE_LOGIC)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_single_function(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"Any": object}
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


def test_beta11_version_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta11"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta11.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "delayedPileSet",
        "current map",
        "Zone 36",
        "circuit breaker",
        "0.4.0-beta7",
    ):
        assert marker in notes


def test_delayed_pile_set_string_values_are_hex_quarter_hours() -> None:
    decode = _load_single_function(COORDINATOR, "_decode_delayed_pile_set_wire")
    expected = {
        "10": 16,  # 4 h
        "14": 20,  # 5 h
        "18": 24,  # 6 h
        "20": 32,  # 8 h
        "24": 36,  # 9 h
        "28": 40,  # 10 h
    }
    for raw, quarters in expected.items():
        assert decode(raw) == quarters

    # Numeric values are already decoded by the source layer.
    assert decode(18) == 18
    assert decode(24.0) == 24
    assert decode(None) is None
    assert decode("") is None
    assert decode("not-a-number") is None


def test_coordinator_uses_one_explicit_delayed_pile_decoder() -> None:
    source = COORDINATOR.read_text(encoding="utf-8")
    assert "def _decode_delayed_pile_set_wire" in source
    assert "rain_delay_wire = _decode_delayed_pile_set_wire(_rd)" in source
    assert "try decimal (set-list style) then hex" not in source


def test_stale_zone_is_not_scheduler_eligible_even_with_old_completion() -> None:
    logic = _load_schedule_logic()
    zones = [
        {
            "id": 36,
            "stale": True,
            "last_completed_at": "2026-10-04T12:00:00+00:00",
        },
        {
            "id": 37,
            "stale": False,
            "last_completed_at": "2026-10-04T12:00:00+00:00",
        },
    ]
    result = logic.filter_schedule_zones(zones, [36, 37])
    assert [row["id"] for row in result] == [37]


def test_scheduler_uses_current_map_as_dispatch_authority() -> None:
    source = SCHEDULE.read_text(encoding="utf-8")
    assert "def _current_map_zone_ids" in source
    assert "def _prune_schedule_to_current_map" in source
    assert 'runtime["current_map_zone_ids"] = sorted(current_ids)' in source
    assert 'runtime["pruned_zone_ids"]' in source
    assert "OPT_SCHEDULE_ZONE_IDS" in source
    assert "OPT_SCHEDULE_CUSTOM_QUEUE" in source
    assert "zone_id not in current_ids" in source
    assert 'suspended_reason"] = "current_map_unavailable"' in source
    assert "Refused scheduler command for zone" in source


def test_removed_active_slot_is_cleared_before_next_dispatch() -> None:
    source = SCHEDULE.read_text(encoding="utf-8")
    start = source.index("def _prune_schedule_to_current_map")
    end = source.index("def _record_mowing_attempt", start)
    block = source[start:end]
    for marker in (
        'runtime["pending_command"] = None',
        'runtime["unfinished"] = False',
        '"zone_removed_from_current_map"',
        'runtime["wait_for_settle_after_prune"] = True',
        "_reset_attempt_tracking(runtime, reset_failures=True)",
    ):
        assert marker in block


def test_rapid_mow_return_loop_has_bounded_circuit_breaker() -> None:
    source = SCHEDULE.read_text(encoding="utf-8")
    start = source.index("def _register_fast_failed_return")
    end = source.index("def _zone_completed_after", start)
    block = source[start:end]
    assert "previous_activity != ACTIVITY_MOWING" in block
    assert "activity != ACTIVITY_RETURNING" in block
    assert "_START_RETURN_FAILURE_WINDOW_SECONDS = 300.0" in source
    assert "_START_RETURN_FAILURE_LIMIT = 2" in source
    assert "_START_RETURN_MIN_PROGRESS_DELTA = 1.0" in source
    assert '"rapid_return_without_progress"' in block
    assert 'runtime["suspended_reason"] = "zone_start_failed"' in block
    assert "loop_guard" in block


def test_loop_guard_does_not_override_known_interruptions() -> None:
    source = SCHEDULE.read_text(encoding="utf-8")
    start = source.index("def _register_fast_failed_return")
    end = source.index("def _zone_completed_after", start)
    block = source[start:end]
    assert "if _interruption_reason(controller, data):" in block
    assert "return False" in block


def test_removed_zone_is_hidden_from_schedule_options() -> None:
    source = CONFIG_FLOW.read_text(encoding="utf-8")
    start = source.index("def _schedule_zone_rows")
    end = source.index("def _schedule_unavailable_text", start)
    block = source[start:end]
    assert '((data.get("map") or {}).get("zones") or [])' in block
    assert 'row.get("stale") is True' in block
    assert "zone_id not in current_ids" in block
    assert "text in current_ids" in block
