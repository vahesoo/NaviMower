from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_vendor_progress_module_is_history_reset_guard_only() -> None:
    source = (COMPONENT / "vendor_progress_semantics.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "_RESET_CONFIRMATIONS_REQUIRED = 2" in source
    assert "new_start > old_start" in source
    assert "live_progress > _RESET_LIVE_CORROBORATION_MAX" in source
    assert "def _filtered_reset_snapshot" in source
    assert "def _apply_vendor_first_zone_state" not in source
    assert "_refresh_zone_model" not in source
    assert 'row["coverage_pct"]' not in source


def test_reset_guard_stays_between_completion_and_map_pipeline() -> None:
    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    completion = runtime.index("install_completion_semantics()")
    vendor = runtime.index("install_vendor_progress_semantics()")
    map_api = runtime.index("install_map_api_performance()")
    assert completion < vendor < map_api


def test_cycle_engine_is_the_only_current_zone_publisher() -> None:
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    ledger = (COMPONENT / "zone_ledger_semantics.py").read_text(encoding="utf-8")
    vendor = (COMPONENT / "vendor_progress_semantics.py").read_text(encoding="utf-8")

    assert "run_zone_ledger_authority(self, snapshot)" in coordinator
    assert 'snapshot["zone_states"] = deepcopy(rows)' in ledger
    assert 'snapshot["totals"] = deepcopy(totals)' in ledger
    assert 'snapshot["zone_states"]' not in vendor
    assert 'snapshot["totals"]' not in vendor
