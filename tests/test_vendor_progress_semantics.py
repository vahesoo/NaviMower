from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_vendor_progress_semantics_is_valid_python_and_wired_after_completion() -> None:
    source = (COMPONENT / "vendor_progress_semantics.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert '"vendor_coverage_monotonic_hold"' in source
    assert '"vendor_first_monotonic_with_confirmed_reset"' in source
    assert "peak_progress" in source
    assert "_RESET_CONFIRMATIONS_REQUIRED = 2" in source
    assert "new_start > old_start" in source
    assert "live_progress > _RESET_LIVE_CORROBORATION_MAX" in source

    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    completion = runtime.index("install_completion_semantics()")
    vendor = runtime.index("install_vendor_progress_semantics()")
    map_api = runtime.index("install_map_api_performance()")
    assert completion < vendor < map_api


def test_vendor_first_layer_uses_path_info_finished_area_without_overwriting_raw_pct() -> None:
    source = (COMPONENT / "vendor_progress_semantics.py").read_text(encoding="utf-8")
    assert 'vendor.get("pct")' in source
    assert 'vendor.get("finished")' in source
    assert 'row["vendor_coverage_pct"]' in source
    assert 'row["coverage_pct"]' in source
    assert 'row["mowed_area_m2"]' in source
    assert 'row["task_progress_pct"]' not in source
