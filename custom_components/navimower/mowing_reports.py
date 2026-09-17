"""Normalize read-only Navimow mowing-report statistics."""
from __future__ import annotations

from typing import Any

MOWING_REPORT_TTL_SECONDS = 3600
MOWING_REPORT_MAIN_PATH = "/vehicle/report/vehicle-main-report"
MOWING_REPORT_PERIOD_PATH = "/vehicle/report/get-day-week-month-data"


def _as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize_period_report(value: Any) -> dict[str, Any]:
    """Return one day/week/month report in stable integration units."""
    data = _as_dict(value)
    mowing_area = _as_dict(data.get("mowingArea"))
    mowing_duration = _as_dict(data.get("mowingDuration"))
    area_m2 = _as_float(mowing_area.get("totalArea"))
    time_h = _as_float(mowing_duration.get("totalDuration"))
    update_message = data.get("updateMessage")
    return {
        "area_m2": area_m2,
        "time_h": time_h,
        "update_message": str(update_message) if update_message else None,
    }


def normalize_total_report(value: Any) -> dict[str, Any]:
    """Return lifetime report counters in stable integration units."""
    data = _as_dict(value)
    return {
        "area_m2": _as_float(data.get("totalMowingArea")),
        "time_h": _as_float(data.get("totalMowingTime")),
        "active_time": data.get("activeTime"),
        "vehicle_code": data.get("vehicleCode"),
    }


def normalize_mowing_reports(
    *,
    query_time: str,
    main: Any,
    day: Any,
    week: Any,
    month: Any,
) -> dict[str, Any]:
    """Build the compact report snapshot used by sensors and diagnostics."""
    return {
        "source": "private_cloud_report",
        "query_time": str(query_time),
        "day": normalize_period_report(day),
        "week": normalize_period_report(week),
        "month": normalize_period_report(month),
        "total": normalize_total_report(main),
    }


def report_value(
    reports: Any,
    period: str,
    field: str,
) -> float | None:
    """Read one normalized numeric report value defensively."""
    if not isinstance(reports, dict):
        return None
    row = reports.get(period)
    if not isinstance(row, dict):
        return None
    return _as_float(row.get(field))
