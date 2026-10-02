from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
PKG = "navimower_authority_test"
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(COMPONENT)]
sys.modules[PKG] = pkg

coordinator_stub = types.ModuleType(f"{PKG}.coordinator")
coordinator_stub.NavimowCoordinator = type("NavimowCoordinator", (), {})
sys.modules[f"{PKG}.coordinator"] = coordinator_stub


def load(name: str):
    path = COMPONENT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


canonical = load("canonical_state")
const = load("const")
authority = load("canonical_authority_semantics")
ledger = load("zone_ledger")
ledger_semantics = load("zone_ledger_semantics")


def test_public_projection_uses_canonical_position_and_task() -> None:
    snapshot = {
        "position": {"x": 1.0, "y": 1.0, "heading": 0.0},
        "totals": {"task_progress_pct": 10.0},
    }
    state = {
        "schema_version": 1,
        "mode": "authoritative_v2",
        "navigation": {
            "position": {
                "value": {"x": 9.0, "y": -2.0, "heading": 1.2},
                "source": "private_cloud",
                "source_age_s": 4.0,
                "stale": False,
            },
            "physical_zone_id": 5,
            "physical_zone_source": "private_cloud",
            "target_zone_id": 5,
            "target_zone_source": "private_work_target",
            "planned_zone_ids": [5],
            "planned_zones_source": "private_current_zones",
            "channel_id": None,
            "channel_source": None,
        },
        "task": {
            "progress_pct": 43.0,
            "mowed_area_m2": 43.0,
            "area_m2": 100.0,
            "zone_ids": [5],
            "active_zone_id": 5,
            "progress_source": "vendor_current_coverage",
            "mowed_area_source": "vendor_current_coverage",
        },
    }

    authority._apply_public_state(snapshot, state)

    assert snapshot["position"] == {"x": 9.0, "y": -2.0, "heading": 1.2}
    assert snapshot["pose_source"] == "private_cloud"
    assert snapshot["totals"]["task_progress_pct"] == 43.0
    assert snapshot["totals"]["task_mowed_area_m2"] == 43.0
    assert snapshot["totals"]["task_zone_ids"] == [5]
    assert snapshot["mowing_progress"] == 43.0
    assert snapshot["session_area"] == 43.0
    assert snapshot["canonical_owner"] == "canonical_mower_state"


class History:
    def active_session_metadata(self):
        return {"id": "active", "zone_ids": [5], "visited_zone_ids": [5]}

    def cycle_diagnostics(self):
        return {}


class Owner:
    history = History()
    _zone_ledger_state = None
    _endpoint_status = {"path_info_time": {"last_success_mono": 1.0}}


def test_zone_ledger_authority_publishes_current_zone_and_totals() -> None:
    owner = Owner()
    snapshot = {
        "activity": "mowing",
        "docked": False,
        "map": {
            "zones": [
                {
                    "id": 5,
                    "name": "Yard",
                    "area_m2": 100.0,
                    "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]],
                }
            ]
        },
        "coverage": {
            "zones": [
                {
                    "id": 5,
                    "pct": 43,
                    "finished": 43.0,
                    "area": 100.0,
                    "start_time": 100,
                }
            ]
        },
        "coverage_source_age": 1,
        "current_zone_ids": [5],
        "target_zone_ids": [5],
        "active_zone_progress_zone_id": 5,
        "mowing_progress": 43,
        "session_area": 43,
        "zone_details": [{"id": 5, "name": "Yard", "area_m2": 100.0}],
        "zone_states": [{"id": 5, "coverage_pct": 99.0, "mowed_area_m2": 99.0}],
        "totals": {
            "map_area_m2": 100.0,
            "map_mowed_area_m2": 99.0,
            "map_coverage_pct": 99.0,
            "task_area_m2": 100.0,
            "task_mowed_area_m2": 99.0,
            "task_progress_pct": 99.0,
        },
    }

    ledger_semantics.run_zone_ledger_authority(owner, snapshot)

    assert snapshot["zone_states"][0]["coverage_pct"] == 43.0
    assert snapshot["totals"]["map_coverage_pct"] == 43.0
    assert snapshot["totals"]["task_progress_pct"] == 43.0
    assert snapshot["mowing_progress"] == 43.0
    assert snapshot["cycle_engine_owner"] == "ZoneLedger"
    assert owner._zone_ledger_state["zones"]["5"]["progress_pct"] == 43.0
    assert snapshot["zone_ledger"]["public_owner"] == "ZoneLedger"
