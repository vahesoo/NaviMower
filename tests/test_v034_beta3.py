"""Regressions for Navimower v0.3.4-beta3."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
PACKAGE = "navimower_v034_beta3_test"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _zone_state_module():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT)]
    sys.modules.setdefault(PACKAGE, package)
    _load_module(f"{PACKAGE}.const", COMPONENT / "const.py")
    return _load_module(f"{PACKAGE}.zone_state", COMPONENT / "zone_state.py")


def _model(*, vendor: float, live: float):
    module = _zone_state_module()
    return module.build_zone_model(
        map_zones=[
            {"id": 13, "name": "Completed", "area": 1000.0},
            {"id": 24, "name": "Active", "area": 100.0},
        ],
        zone_details=[
            {
                "id": 13,
                "name": "Completed",
                "area_m2": 1000.0,
                "percentage": 100,
                "vendor_percentage": 100,
            },
            {
                "id": 24,
                "name": "Active",
                "area_m2": 100.0,
                "percentage": vendor,
                "vendor_percentage": vendor,
                "progress": live,
                "progress_source": "mqtt_map_work_position",
            },
        ],
        coverage={
            "zones": [
                {"id": 13, "area": 1000.0, "finished": 1000.0, "pct": 100},
                {"id": 24, "area": 100.0, "finished": vendor, "pct": vendor},
            ]
        },
        zone_history={},
        active_session={
            "id": "active-cycle",
            "zone_ids": [13, 24],
            "visited_zone_ids": [13, 24],
            "task_zone_progress": {"13": 100, "24": 100},
        },
        active_zone_id=24,
    )


def test_stale_completed_active_zone_uses_fresh_live_progress() -> None:
    zones, totals = _model(vendor=12, live=29)
    active = next(row for row in zones if row["id"] == 24)
    assert active["coverage_pct"] == 29.0
    assert active["task_progress_pct"] == 29.0
    assert active["mowed_area_m2"] == 29.0
    assert active["progress_source"] == "active_live_recovery"
    assert active["progress_recovered"] is True
    assert totals["completed_zone_count"] == 1
    assert totals["map_coverage_pct"] == 93.5


def test_completed_vendor_coverage_keeps_monotonic_task_value() -> None:
    zones, _totals = _model(vendor=100, live=29)
    active = next(row for row in zones if row["id"] == 24)
    assert active["coverage_pct"] == 100.0
    assert active["task_progress_pct"] == 100.0
    assert active["progress_recovered"] is False


def test_history_heals_stale_completion_and_false_completed_flag() -> None:
    source = (COMPONENT / "history.py").read_text()
    assert '"task_zone_last_evidence"' in source
    assert '"task_zone_completion_candidates"' in source
    assert "previous_progress" in source
    assert 'active.get("completion_reason") == "vendor_progress"' in source
    assert 'active["completed"] = None' in source


def test_map_data_uses_metadata_only_session_index() -> None:
    source = (COMPONENT / "sensor.py").read_text()
    block = source.split("class NavimowerMapDataSensor", 1)[1]
    assert "sessions_index_payload()" in block
    assert "history.active_session" not in block
    assert "session_summaries(" not in block
    assert 'session_index.get("active_session_id")' in block
