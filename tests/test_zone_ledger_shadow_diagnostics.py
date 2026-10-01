from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
PKG = "navimower_cutover_diag_test"
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT / "custom_components" / "navimower")]
sys.modules[PKG] = pkg

coordinator_stub = types.ModuleType(f"{PKG}.coordinator")
coordinator_stub.NavimowCoordinator = type("NavimowCoordinator", (), {})
sys.modules[f"{PKG}.coordinator"] = coordinator_stub

for module_name in ("zone_ledger", "zone_ledger_semantics"):
    path = ROOT / "custom_components" / "navimower" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"{PKG}.{module_name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

semantics = sys.modules[f"{PKG}.zone_ledger_semantics"]


def test_task_area_only_addition_is_classified_as_enrichment() -> None:
    report = semantics.build_cutover_diagnostics(
        legacy_rows=[{"id": 36, "coverage_pct": 74.0, "mowed_area_m2": 1222.52}],
        legacy_totals={
            "map_area_m2": 1733.8,
            "map_mowed_area_m2": 1304.27,
            "map_coverage_pct": 75.2,
            "task_area_m2": None,
            "task_mowed_area_m2": 1224.97,
            "task_progress_pct": 74.0,
        },
        ledger_rows=[{"id": 36, "coverage_pct": 74.0, "mowed_area_m2": 1222.52}],
        ledger_totals={
            "map_area_m2": 1733.8,
            "map_mowed_area_m2": 1304.27,
            "map_coverage_pct": 75.2,
            "task_area_m2": 1652.05,
            "task_mowed_area_m2": 1224.97,
            "task_progress_pct": 74.0,
        },
        ledger_task={"area_m2": 1652.05},
        ledger_state={"revision": 1},
        events=[],
    )
    assert report["match"] is True
    assert report["strict_match"] is False
    assert report["metric_match"] is True
    assert report["metric_differences"] == {}
    assert report["enrichments"]["task_area_m2"]["classification"] == "canonical_enrichment"


def test_real_zone_regression_still_fails_compatibility_parity() -> None:
    report = semantics.build_cutover_diagnostics(
        legacy_rows=[{"id": 140, "coverage_pct": 100.0, "mowed_area_m2": 281.29}],
        legacy_totals={
            "map_area_m2": 281.29,
            "map_mowed_area_m2": 281.29,
            "map_coverage_pct": 100.0,
            "task_area_m2": None,
            "task_mowed_area_m2": 0.0,
            "task_progress_pct": 0.0,
        },
        ledger_rows=[{"id": 140, "coverage_pct": 0.0, "mowed_area_m2": 0.0}],
        ledger_totals={
            "map_area_m2": 281.29,
            "map_mowed_area_m2": 0.0,
            "map_coverage_pct": 0.0,
            "task_area_m2": None,
            "task_mowed_area_m2": 0.0,
            "task_progress_pct": 0.0,
        },
        ledger_task={},
        ledger_state={"revision": 1},
        events=[],
    )
    assert report["match"] is False
    assert report["zone_match"] is False
    assert report["zone_differences"][0]["zone_id"] == 140
