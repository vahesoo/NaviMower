from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "custom_components" / "navimower" / "canonical_state.py"

spec = importlib.util.spec_from_file_location("navimower_canonical_state_test", MODULE_PATH)
assert spec and spec.loader
canonical = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = canonical
spec.loader.exec_module(canonical)


def _base_snapshot() -> dict:
    return {
        "activity": "mowing",
        "state": "Mowing",
        "state_code": "0210",
        "docked": False,
        "error": False,
        "mqtt_connected": True,
        "mqtt_stream_state": "pose_degraded",
        "current_physical_zone_id": 5,
        "current_physical_zone_source": "private_cloud",
        "target_zone_id": 5,
        "target_zone_source": "private_work_target",
        "planned_zone_ids": [5],
        "planned_zones_source": "private_current_zones",
        "current_channel_id": None,
        "current_channel_source": None,
        "map": {"revision": "1|2|3", "zones": [{"id": 5}]},
    }


def _ledger() -> tuple[dict, dict]:
    state = {
        "version": 1,
        "revision": 7,
        "zones": {
            "5": {
                "id": 5,
                "cycle_key": "vendor:5:100",
                "progress_pct": 43.0,
                "mowed_area_m2": 43.0,
                "completed_current_cycle": False,
                "pending_vendor_cycle": False,
                "progress_source": "vendor_coverage",
                "source_age_s": 4.0,
                "stale": False,
            }
        },
    }
    task = {
        "progress_pct": 43.0,
        "mowed_area_m2": 43.0,
        "area_m2": 100.0,
        "zone_ids": [5],
        "active_zone_id": 5,
        "progress_source": "private_task_percentage",
        "mowed_area_source": "private_cloud",
    }
    return state, task


def test_sparse_h1_mqtt_falls_back_to_private_cloud_without_losing_cycle() -> None:
    snapshot = _base_snapshot()
    snapshot["mqtt_pose_age"] = 3600
    ledger_state, ledger_task = _ledger()
    state = canonical.build_canonical_state(
        snapshot,
        ledger_state=ledger_state,
        ledger_task=ledger_task,
        vendor_owned_zone_ids={5},
        vendor_store_revision=11,
        mqtt_position={"x": 1.0, "y": 1.0, "heading": 0.0},
        cloud_position={"x": 7.5, "y": -0.4, "heading": 1.2},
        cloud_position_age_s=6.0,
    )
    assert state["navigation"]["position"]["source"] == "private_cloud"
    assert state["health"]["mqtt_pose_sparse_or_stale"] is True
    assert state["cycles"]["rows"][0]["vendor_geometry_owned"] is True
    assert "parity" not in state


def test_dense_mqtt_position_wins_over_cloud() -> None:
    snapshot = _base_snapshot()
    snapshot["mqtt_pose_age"] = 0.4
    ledger_state, ledger_task = _ledger()
    state = canonical.build_canonical_state(
        snapshot,
        ledger_state=ledger_state,
        ledger_task=ledger_task,
        mqtt_position={"x": 9.0, "y": 3.0, "heading": 0.3},
        cloud_position={"x": 8.0, "y": 2.0, "heading": 0.2},
        cloud_position_age_s=4.0,
    )
    assert state["navigation"]["position"]["source"] == "official_mqtt"
    assert state["health"]["mqtt_pose_sparse_or_stale"] is False


def test_diagnostics_never_export_exact_position_coordinates() -> None:
    snapshot = _base_snapshot()
    snapshot["mqtt_pose_age"] = 1.0
    ledger_state, ledger_task = _ledger()
    state = canonical.build_canonical_state(
        snapshot,
        ledger_state=ledger_state,
        ledger_task=ledger_task,
        mqtt_position={"x": 123.456, "y": -987.654, "heading": 0.3},
    )
    report = canonical.canonical_diagnostics(state)
    assert report is not None
    assert report["position"]["coordinates_included"] is False
    assert "value" not in report["position"]
    assert "123.456" not in repr(report)
    assert "987.654" not in repr(report)


def test_task_authority_uses_zone_ledger_task() -> None:
    snapshot = _base_snapshot()
    snapshot["mqtt_pose_age"] = 1
    ledger_state, ledger_task = _ledger()
    state = canonical.build_canonical_state(
        snapshot,
        ledger_state=ledger_state,
        ledger_task=ledger_task,
        mqtt_position={"x": 0, "y": 0},
    )
    assert state["task"]["source"] == "zone_ledger_task"
    assert state["task"]["progress_pct"] == 43.0
    assert state["task"]["zone_ids"] == [5]


def test_missing_mqtt_pose_reports_private_cloud_fallback_reason() -> None:
    snapshot = _base_snapshot()
    snapshot["mqtt_pose_age"] = None
    ledger_state, ledger_task = _ledger()
    state = canonical.build_canonical_state(
        snapshot,
        ledger_state=ledger_state,
        ledger_task=ledger_task,
        cloud_position={"x": 7.5, "y": -0.4, "heading": 1.2},
        cloud_position_age_s=8.0,
    )
    assert state["health"]["mqtt_pose_seen"] is False
    assert state["health"]["mqtt_pose_available"] is False
    assert state["health"]["position_fallback_reason"] == "mqtt_pose_missing"


def test_no_legacy_position_or_task_bridge_exists() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "legacy_resolved_fallback" not in source
    assert "legacy_totals_bridge" not in source
    assert "ledger_diagnostics" not in source

def test_docked_without_fresh_mqtt_keeps_real_cloud_pose_and_heading() -> None:
    snapshot = _base_snapshot()
    snapshot["docked"] = True
    snapshot["mqtt_pose_age"] = 3600
    state = canonical.build_canonical_state(
        snapshot,
        mqtt_position={"x": 10.0, "y": 10.0, "heading": 0.3},
        cloud_position={"x": 1.8, "y": 2.7, "heading": 0.2},
        cloud_position_age_s=2.0,
    )
    position = state["navigation"]["position"]
    assert position["source"] == "private_cloud"
    assert position["value"] == {"x": 1.8, "y": 2.7, "heading": 0.2}
    assert state["health"]["position_fallback_reason"] == "mqtt_pose_stale"
