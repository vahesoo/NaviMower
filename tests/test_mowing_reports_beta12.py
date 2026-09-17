from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
REPORTS = COMPONENT / "mowing_reports.py"
SEMANTICS = COMPONENT / "mowing_report_semantics.py"
RUNTIME = COMPONENT / "runtime.py"
MANIFEST = COMPONENT / "manifest.json"


def _load_reports_module():
    spec = importlib.util.spec_from_file_location("navimower_mowing_reports_test", REPORTS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_beta12_normalizes_proven_day_week_month_and_total_shapes() -> None:
    module = _load_reports_module()
    period = {
        "mowingArea": {"totalArea": "244.3"},
        "mowingDuration": {"totalDuration": "1.0"},
        "updateMessage": "Latest updates: Today, 17:00",
    }
    reports = module.normalize_mowing_reports(
        query_time="2026-9-17",
        main={"totalMowingArea": "498189.4", "totalMowingTime": "2065.9"},
        day=period,
        week=period,
        month={
            "mowingArea": {"totalArea": "35438.8"},
            "mowingDuration": {"totalDuration": "140.4"},
        },
    )

    assert reports["day"]["area_m2"] == 244.3
    assert reports["day"]["time_h"] == 1.0
    assert reports["week"]["area_m2"] == 244.3
    assert reports["month"]["area_m2"] == 35438.8
    assert reports["month"]["time_h"] == 140.4
    assert reports["total"]["area_m2"] == 498189.4
    assert reports["total"]["time_h"] == 2065.9
    assert module.report_value(reports, "week", "area_m2") == 244.3


def test_beta12_invalid_or_negative_report_values_never_publish() -> None:
    module = _load_reports_module()
    reports = module.normalize_mowing_reports(
        query_time="2026-9-17",
        main={"totalMowingArea": "bad", "totalMowingTime": -1},
        day={"mowingArea": {"totalArea": None}},
        week={},
        month={},
    )
    assert reports["total"]["area_m2"] is None
    assert reports["total"]["time_h"] is None
    assert reports["day"]["area_m2"] is None


def test_beta12_uses_production_report_endpoints_with_hourly_last_good_cache() -> None:
    source = SEMANTICS.read_text(encoding="utf-8")
    reports = REPORTS.read_text(encoding="utf-8")
    assert 'MOWING_REPORT_TTL_SECONDS = 3600' in reports
    assert 'MOWING_REPORT_MAIN_PATH = "/vehicle/report/vehicle-main-report"' in reports
    assert 'MOWING_REPORT_PERIOD_PATH = "/vehicle/report/get-day-week-month-data"' in reports
    assert 'coordinator._fetch_endpoint(' in source
    for query_type in (1, 2, 3):
        assert f'"query_type": {query_type}' in source
    assert 'snapshot["weekly_area_source"] = "private_cloud_report"' in source
    assert 'snapshot["weekly_area_source"] = "legacy_location_fallback"' in source


def test_beta12_report_sensor_group_is_consistent_and_disabled_by_default() -> None:
    source = SEMANTICS.read_text(encoding="utf-8")
    expected = (
        ("daily_mowed_area", "Mowed area today"),
        ("weekly_mowed_area", "Mowed area this week"),
        ("monthly_mowed_area", "Mowed area this month"),
        ("total_mowed_area", "Mowed area total"),
        ("daily_mowed_time", "Mowed time today"),
        ("weekly_mowed_time", "Mowed time this week"),
        ("monthly_mowed_time", "Mowed time this month"),
        ("total_mowed_time", "Mowed time total"),
    )
    for key, name in expected:
        assert f'key="{key}"' in source
        assert f'name="{name}"' in source
    assert source.count("entity_registry_enabled_default=False") >= len(expected)
    # The existing weekly entity keeps its stable unique-ID key and is replaced
    # in-place rather than duplicated or removed.
    assert 'if description.key == "weekly_mowed_area":' in source


def test_beta12_runtime_remains_installed_on_later_0_4_5_betas() -> None:
    runtime = RUNTIME.read_text(encoding="utf-8")
    assert "from .mowing_report_semantics import install_mowing_report_semantics" in runtime
    assert "install_mowing_report_semantics()" in runtime
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"].startswith("0.4.5-beta")
