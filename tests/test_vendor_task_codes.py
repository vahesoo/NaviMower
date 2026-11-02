"""Tests for official vendor task-state and MowerWorkInfo diagnostics."""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "custom_components" / "navimower" / "vendor_task_codes.py"


def _module():
    spec = importlib.util.spec_from_file_location("vendor_task_codes_test", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _owner(**overrides):
    now = time.monotonic()
    owner = SimpleNamespace(
        _endpoint_status={
            "today_plan": {"last_success_mono": now - 3},
            "location": {"last_success_mono": now - 5},
        },
        _mqtt_location={"mow_start_type": 1, "work_mode": 0},
        _mqtt_start_type_last_update=now - 2,
        _mqtt_work_start_type_last_update=now - 4,
    )
    for key, value in overrides.items():
        setattr(owner, key, value)
    return owner


def test_known_vendor_symbols_and_distinct_sources():
    module = _module()
    snapshot = {
        "raw": {"today_plan": {"m_task_status": "8", "c_plan_status": "9", "undrawnPileReason": "0A"},
                "location": {"map_work_position": "0020"}},
        "work_mode": 1,
    }
    report = module.vendor_task_codes_diagnostics(_owner(), snapshot)
    task = report["mower_task"]
    assert task["day_state"]["symbol"] == "TASK_MOW_PLAN_LOOP_WAITING"
    assert task["day_state"]["value"] == 8
    assert task["current_plan_status"]["value"] == 9
    assert "NOT" in task["current_plan_status"]["note"]
    assert task["loop_wait_reason"]["symbol"] == "AFTER_SNOW"
    work = report["mower_work_info"]
    assert work["mqtt_explicit_start_type"]["symbol"] == "MOW_BY_USER"
    assert work["mqtt_packed_start_type"]["symbol"] == "MOW_BY_AUTO"
    assert work["private_packed_start_type"]["symbol"] == "MOW_BY_USER"
    assert work["mqtt_packed_start_type"]["source_age_s"] >= 0


def test_manual_task_wait_and_unknowns_preserved():
    module = _module()
    a = module.vendor_task_codes_diagnostics(
        _owner(_mqtt_location={"mow_start_type": -1}),
        {"raw": {"today_plan": {"m_task_status": "9", "undrawnPileReason": "10"}}},
    )
    assert a["mower_task"]["day_state"]["symbol"] == "TASK_MOW_MANUAL_LOOP_WAITING"
    assert a["mower_task"]["loop_wait_reason"]["value"] == 16
    assert a["mower_task"]["loop_wait_reason"]["symbol"] is None
    assert a["mower_work_info"]["mqtt_explicit_start_type"]["symbol"] == "MOW_BY_UNKNOWN"
    assert a["mower_work_info"]["mqtt_packed_start_type"]["value"] is None
    b = module.vendor_task_codes_diagnostics(_owner(_mqtt_location={}), {})
    assert b["mower_task"]["day_state"]["value"] is None
    assert b["mower_task"]["day_state"]["recognized"] is False
    assert b["mower_work_info"]["private_packed_start_type"]["source"] is None


def test_invalid_vendor_values_remain_unknown_not_guessed():
    module = _module()
    assert module._decimal_code("0x8") is None
    assert module._decimal_code("8.0") is None
    assert module._decimal_code(True) is None
    assert module._hex_wait_reason("0A") == 10
    assert module._hex_wait_reason("10") == 16
    assert module._decimal_code("7") == 7
    assert module.TASK_DAY_STATES[7] == "TASK_MOW_BY_USER"


def test_diagnostics_wired_without_any_command_or_extra_poll():
    source = (ROOT / "custom_components" / "navimower" / "diagnostics.py").read_text()
    assert '"vendor_task_codes": vendor_task_codes_diagnostics(coordinator, data)' in source
    assert "from .vendor_task_codes import vendor_task_codes_diagnostics" in source
    code = PATH.read_text()
    assert ".async_send(" not in code
    assert ".today_plan(" not in code
    assert ".call(" not in code
