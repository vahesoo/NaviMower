"""Production polling and optional Home Assistant sensors for mowing reports."""
from __future__ import annotations

from copy import deepcopy
import time
from typing import Any, Callable

from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import UnitOfArea, UnitOfTime
from homeassistant.util import dt as dt_util

from . import sensor as platform
from .coordinator_semantics import NavimowCoordinator
from .mowing_reports import (
    MOWING_REPORT_MAIN_PATH,
    MOWING_REPORT_PERIOD_PATH,
    MOWING_REPORT_TTL_SECONDS,
    normalize_mowing_reports,
    report_value,
)

_INSTALLED = False
_REPORT_KEYS = ("report_main", "report_day", "report_week", "report_month")
_PERIOD_KEYS = ("report_day", "report_week", "report_month")


def _query_time() -> str:
    """Return the vendor report date in Home Assistant's configured timezone."""
    today = dt_util.now().date()
    return f"{today.year}-{today.month}-{today.day}"


def _wrapped_data(raw: dict[str, Any], key: str, query_time: str | None = None) -> Any:
    """Return cached report data, optionally requiring the current query date."""
    wrapped = raw.get(key)
    if not isinstance(wrapped, dict):
        return {}
    if query_time is not None and wrapped.get("query_time") != query_time:
        return {}
    data = wrapped.get("data")
    return data if isinstance(data, dict) else {}


def _refresh_mowing_reports(coordinator: NavimowCoordinator, snapshot: dict[str, Any]) -> None:
    """Refresh hourly vendor report counters without disturbing core polling."""
    query_time = _query_time()
    previous_query_time = getattr(coordinator, "_mowing_report_query_time", None)
    if previous_query_time != query_time:
        # A new local calendar day must not wait for yesterday's endpoint TTL.
        for key in _PERIOD_KEYS:
            status = coordinator._endpoint_status.get(key)
            if isinstance(status, dict):
                status["last_attempt_mono"] = None
        coordinator._mowing_report_query_time = query_time

    language = str(getattr(coordinator.client, "_language", "en") or "en")
    base = {"vehicle_sn": coordinator.sn, "language": language}
    now = time.monotonic()
    getters: dict[str, Callable[[], Any]] = {
        "report_main": lambda: {
            "query_time": query_time,
            "data": coordinator.client.call(MOWING_REPORT_MAIN_PATH, base) or {},
        },
        "report_day": lambda: {
            "query_time": query_time,
            "data": coordinator.client.call(
                MOWING_REPORT_PERIOD_PATH,
                {**base, "query_type": 1, "query_time": query_time},
            )
            or {},
        },
        "report_week": lambda: {
            "query_time": query_time,
            "data": coordinator.client.call(
                MOWING_REPORT_PERIOD_PATH,
                {**base, "query_type": 2, "query_time": query_time},
            )
            or {},
        },
        "report_month": lambda: {
            "query_time": query_time,
            "data": coordinator.client.call(
                MOWING_REPORT_PERIOD_PATH,
                {**base, "query_type": 3, "query_time": query_time},
            )
            or {},
        },
    }
    for key, getter in getters.items():
        coordinator._fetch_endpoint(
            coordinator._raw_cache,
            key,
            getter,
            ttl=MOWING_REPORT_TTL_SECONDS,
            now=now,
        )

    raw = coordinator._raw_cache
    reports = normalize_mowing_reports(
        query_time=query_time,
        main=_wrapped_data(raw, "report_main"),
        day=_wrapped_data(raw, "report_day", query_time),
        week=_wrapped_data(raw, "report_week", query_time),
        month=_wrapped_data(raw, "report_month", query_time),
    )
    snapshot["mowing_reports"] = reports

    # Preserve the existing weekly_mowed_area entity/unique ID. The proven
    # report endpoint becomes canonical, while the old location counter remains
    # a first-start/unsupported-firmware fallback so the entity never disappears.
    weekly_area = report_value(reports, "week", "area_m2")
    if weekly_area is not None:
        snapshot["weekly_area"] = weekly_area
        snapshot["weekly_area_source"] = "private_cloud_report"
    elif snapshot.get("weekly_area") is not None:
        snapshot["weekly_area_source"] = "legacy_location_fallback"
    else:
        snapshot["weekly_area_source"] = None

    # Home Assistant diagnostics already export the sanitized raw snapshot.
    # Add the report cache rows here because the base parser ran before this
    # bounded report refresh wrapper.
    diagnostic_raw = snapshot.get("raw")
    if isinstance(diagnostic_raw, dict):
        for key in _REPORT_KEYS:
            if key in raw:
                diagnostic_raw[key] = deepcopy(raw[key])


def _report_sensor_value(period: str, field: str) -> Callable[[dict], Any]:
    return lambda data: report_value(data.get("mowing_reports"), period, field)


def _report_sensor_attrs(period: str) -> Callable[[dict], dict[str, Any]]:
    def attrs(data: dict) -> dict[str, Any]:
        reports = data.get("mowing_reports") or {}
        row = reports.get(period) if isinstance(reports, dict) else {}
        row = row if isinstance(row, dict) else {}
        result: dict[str, Any] = {
            "source": reports.get("source") if isinstance(reports, dict) else None,
            "query_time": reports.get("query_time") if isinstance(reports, dict) else None,
        }
        if period != "total":
            result["update_message"] = row.get("update_message")
        return result

    return attrs


def _weekly_area_value(data: dict) -> Any:
    return data.get("weekly_area")


def _weekly_area_attrs(data: dict) -> dict[str, Any]:
    reports = data.get("mowing_reports") or {}
    row = reports.get("week") if isinstance(reports, dict) else {}
    row = row if isinstance(row, dict) else {}
    return {
        "source": data.get("weekly_area_source"),
        "query_time": reports.get("query_time") if isinstance(reports, dict) else None,
        "update_message": row.get("update_message"),
    }


def _report_sensor_descriptions() -> tuple[platform.NavimowSensorDescription, ...]:
    """Return the disabled-by-default report sensor group in display order."""
    total = SensorStateClass.TOTAL
    increasing = SensorStateClass.TOTAL_INCREASING
    return (
        platform.NavimowSensorDescription(
            key="daily_mowed_area",
            name="Mowed area today",
            icon="mdi:calendar-today",
            native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
            state_class=total,
            entity_registry_enabled_default=False,
            value_fn=_report_sensor_value("day", "area_m2"),
            attrs_fn=_report_sensor_attrs("day"),
        ),
        platform.NavimowSensorDescription(
            key="weekly_mowed_area",
            name="Mowed area this week",
            icon="mdi:calendar-week",
            native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
            state_class=total,
            entity_registry_enabled_default=False,
            value_fn=_weekly_area_value,
            attrs_fn=_weekly_area_attrs,
        ),
        platform.NavimowSensorDescription(
            key="monthly_mowed_area",
            name="Mowed area this month",
            icon="mdi:calendar-month",
            native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
            state_class=total,
            entity_registry_enabled_default=False,
            value_fn=_report_sensor_value("month", "area_m2"),
            attrs_fn=_report_sensor_attrs("month"),
        ),
        platform.NavimowSensorDescription(
            key="total_mowed_area",
            name="Mowed area total",
            icon="mdi:counter",
            native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
            state_class=increasing,
            entity_registry_enabled_default=False,
            value_fn=_report_sensor_value("total", "area_m2"),
            attrs_fn=_report_sensor_attrs("total"),
        ),
        platform.NavimowSensorDescription(
            key="daily_mowed_time",
            name="Mowed time today",
            icon="mdi:clock-outline",
            native_unit_of_measurement=UnitOfTime.HOURS,
            state_class=total,
            entity_registry_enabled_default=False,
            value_fn=_report_sensor_value("day", "time_h"),
            attrs_fn=_report_sensor_attrs("day"),
        ),
        platform.NavimowSensorDescription(
            key="weekly_mowed_time",
            name="Mowed time this week",
            icon="mdi:calendar-clock",
            native_unit_of_measurement=UnitOfTime.HOURS,
            state_class=total,
            entity_registry_enabled_default=False,
            value_fn=_report_sensor_value("week", "time_h"),
            attrs_fn=_report_sensor_attrs("week"),
        ),
        platform.NavimowSensorDescription(
            key="monthly_mowed_time",
            name="Mowed time this month",
            icon="mdi:calendar-month-outline",
            native_unit_of_measurement=UnitOfTime.HOURS,
            state_class=total,
            entity_registry_enabled_default=False,
            value_fn=_report_sensor_value("month", "time_h"),
            attrs_fn=_report_sensor_attrs("month"),
        ),
        platform.NavimowSensorDescription(
            key="total_mowed_time",
            name="Mowed time total",
            icon="mdi:timer-sand-complete",
            native_unit_of_measurement=UnitOfTime.HOURS,
            state_class=increasing,
            entity_registry_enabled_default=False,
            value_fn=_report_sensor_value("total", "time_h"),
            attrs_fn=_report_sensor_attrs("total"),
        ),
    )


def _install_report_sensors() -> None:
    group = _report_sensor_descriptions()
    result: list[platform.NavimowSensorDescription] = []
    inserted = False
    for description in platform.SENSORS:
        if description.key == "weekly_mowed_area":
            if not inserted:
                result.extend(group)
                inserted = True
            continue
        result.append(description)
    if not inserted:
        result.extend(group)
    platform.SENSORS = tuple(result)


def install_mowing_report_semantics() -> None:
    """Install hourly report polling and stable optional report sensors once."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_fetch_blocking = NavimowCoordinator._fetch_blocking

    def fetch_blocking(self: NavimowCoordinator) -> dict[str, Any]:
        snapshot = original_fetch_blocking(self)
        _refresh_mowing_reports(self, snapshot)
        return snapshot

    NavimowCoordinator._fetch_blocking = fetch_blocking  # type: ignore[method-assign]
    _install_report_sensors()
    _INSTALLED = True
