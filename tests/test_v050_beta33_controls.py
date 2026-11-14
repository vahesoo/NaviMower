"""beta33: MQTT/cloud pause agreement and retained task identity."""
from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

from test_v050_beta31_pause_resume import _load_resume

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "navimower"


def _paused(*, mqtt=3, name="isPaused", code="0210"):
    return {
        "state_code": code,
        "activity": "mowing",  # Can be optimistic while cloud lags.
        "mqtt_vehicle_state": mqtt,
        "mqtt_named_state": name,
        "docked": False,
        "totals": {"task_zone_ids": [43], "active_zone_id": 43},
    }


def test_fresh_named_mqtt_pause_outweighs_lagging_cloud_mowing():
    mod = _load_resume()
    state = _paused()
    assert mod.confirmed_paused_vendor_task(state)
    assert mod.paused_mow_request_decision(state, [43], reset=False) == "vendor_resume"
    assert mod.paused_mow_request_decision(state, [44], reset=False) == "blocked"
    assert mod.task_resume_decision(state)["strategy"] == "vendor"


def test_mqtt_three_is_ambiguous_when_no_task_exists():
    mod = _load_resume()
    state = {
        "mqtt_vehicle_state": 3, "mqtt_named_state": None,
        "activity": "docked", "state_code": "0101", "totals": {},
    }
    assert not mod.confirmed_paused_vendor_task(state)
    assert mod.paused_mow_request_decision(state, [43], reset=False) == "mow"


def test_fresh_running_mqtt_overrides_stale_vendor_pause():
    mod = _load_resume()
    state = _paused(mqtt=4, name="isRunning", code="0211")
    assert not mod.confirmed_paused_vendor_task(state)
    assert mod.paused_mow_request_decision(state, [44], reset=False) == "mow"
    assert mod.task_resume_decision(state)["reason"] == "already_mowing"


def test_retained_order_is_not_replayed_for_newer_vendor_cycle():
    mod = _load_resume()
    run = {
        "started_at": "2026-11-02T10:00:00+00:00",
        "zone_ids": [43], "remaining_zone_ids": [43],
        "resumable": True, "complete": False,
    }
    snapshot = {
        "state_code": "0101", "activity": "docked", "docked": True,
        "mowing_progress": 18, "totals": {"task_zone_ids": [43]},
        "last_ordered_run": run,
        "zone_states": [
            {"id": 43, "vendor_start_time": int(datetime(2026, 11, 14, tzinfo=UTC).timestamp())},
        ],
    }
    assert not mod.ordered_run_matches_current_task(snapshot, run)
    decision = mod.task_resume_decision(snapshot)
    assert decision["strategy"] != "ordered_run"
    assert "ordered_run_superseded_by_newer_vendor_task" in decision["evidence"]


def test_current_ordered_run_still_matches_vendor_cycle():
    mod = _load_resume()
    start = datetime(2026, 11, 14, 12, 17, tzinfo=UTC)
    run = {"started_at": start.isoformat(), "zone_ids": [43]}
    snapshot = {"totals": {"task_zone_ids": [43]},
                "zone_states": [{"id": 43, "vendor_start_time": int(start.timestamp()) + 12}]}
    assert mod.ordered_run_matches_current_task(snapshot, run)
    snapshot["zone_states"][0]["vendor_start_time"] += 3600
    assert not mod.ordered_run_matches_current_task(snapshot, run)


def test_controls_use_one_paused_decision_and_recheck_before_replay():
    services = (ROOT / "services.py").read_text()
    mower = (ROOT / "lawn_mower.py").read_text()
    coordinator = (ROOT / "coordinator.py").read_text()
    assert 'if not confirmed_paused_vendor_task(command_snapshot):' in services
    assert 'if mqtt_state == 4 or activity == ACTIVITY_MOWING:' in services
    assert 'if mqtt_state == 5 or activity == ACTIVITY_RETURNING:' in services
    assert 'paused_action = paused_mow_request_decision(' in services
    assert 'if not ordered_run_matches_current_task(' in services
    assert 'if confirmed_paused_vendor_task(self.data):' in mower
    assert 'snapshot["mqtt_named_state"] = self._fresh_mqtt_named_state()' in coordinator
    assert 'snapshot["activity"] = ACTIVITY_PAUSED' in coordinator

def test_mow_returning_guard_has_bound_activity_constant() -> None:
    """The RETURNING safety guard must resolve its name at runtime."""
    tree = ast.parse((ROOT / "services.py").read_text(encoding="utf-8"))
    imported_constants = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "const"
        for alias in node.names
    }
    assert "ACTIVITY_RETURNING" in imported_constants

