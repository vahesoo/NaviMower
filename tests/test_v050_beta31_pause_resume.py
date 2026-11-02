"""beta31: pause-first vendor Resume and confirmed-run persistence."""
from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _load_resume():
    package = "navimower_beta31"
    mod = types.ModuleType(package)
    mod.__path__ = [str(COMPONENT)]
    sys.modules[package] = mod
    c = types.ModuleType(f"{package}.const")
    c.ACTIVITY_DOCKED = "docked"
    c.ACTIVITY_ERROR = "error"
    c.ACTIVITY_MOWING = "mowing"
    c.ACTIVITY_PAUSED = "paused"
    c.ACTIVITY_RETURNING = "returning"
    c.MAP_EDIT_STATES = {"0202"}
    c.STATE_PAUSED = "0211"
    sys.modules[c.__name__] = c
    spec = importlib.util.spec_from_file_location(
        f"{package}.task_resume", COMPONENT / "task_resume.py"
    )
    obj = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = obj
    spec.loader.exec_module(obj)
    return obj


def test_paused_vendor_resume_wins_over_retained_order_and_optimistic_activity():
    mod = _load_resume()
    data = {
        "state_code": "0211", "activity": "mowing", "mqtt_vehicle_state": 3,
        "totals": {"task_zone_ids": [43]}, "mowing_progress": 77,
        "last_ordered_run": {
            "zone_ids": [43], "remaining_zone_ids": [43],
            "resumable": True, "complete": False,
        },
    }
    result = mod.task_resume_decision(data)
    assert result["available"] is True
    assert result["strategy"] == "vendor"
    assert result["reason"] == "vendor_paused_task"
    assert mod.paused_mow_request_decision(data, [43], reset=False) == "vendor_resume"
    assert mod.paused_mow_request_decision(data, [44], reset=False) == "blocked"
    assert mod.paused_mow_request_decision(data, [43], reset=True) == "mow"
    assert mod.paused_mow_request_decision(data, [], reset=False) == "blocked"


def test_fresh_vendor_mowing_avoids_old_paused_code():
    mod = _load_resume()
    data = {"state_code": "0211", "activity": "mowing", "mqtt_vehicle_state": 4}
    assert mod.task_resume_decision(data)["reason"] == "already_mowing"
    assert mod.paused_mow_request_decision(data, [43], reset=False) == "mow"


def test_control_paths_route_to_vendor_resume_without_auto_reset():
    services = (COMPONENT / "services.py").read_text()
    mower = (COMPONENT / "lawn_mower.py").read_text()
    coord = (COMPONENT / "coordinator.py").read_text()
    for source in (services, mower, coord):
        ast.parse(source)
    assert 'paused_action = paused_mow_request_decision(' in services
    assert 'source="navimower.mow_paused"' in services
    assert 'if paused_action == "blocked":' in services
    assert 'if strategy == RESUME_STRATEGY_VENDOR:' in services
    assert "coordinator.defer_mow_cycle_reset(source=" in services
    assert "self.coordinator.defer_mow_cycle_reset(" in mower
    assert "def defer_mow_cycle_reset(" in coord
    assert "if physical_confirmation == \"state_observed\":" in coord
    assert "self._record_successful_mow_in_ordered_run()" in coord
    assert "self.clear_pending_activity()" in coord
    assert 'pending_activity == ACTIVITY_MOWING' in coord
    sent = coord.split("def record_mow_command_result(", 1)[1].split(
        "def defer_mow_cycle_reset(", 1
    )[0]
    assert "self._record_successful_mow_in_ordered_run()" not in sent
