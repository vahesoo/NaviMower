"""Run the canonical ZoneLedger beside the legacy zone model without publishing it.

ZoneLedger is authoritative for retained trail cycle/reset identity. Numeric
sensor migration remains a shadow comparison against the existing public model.
The ledger and VendorTrailStore are persisted together before restart recovery.
"""
from __future__ import annotations

from copy import deepcopy
import logging
import time
from typing import Any

from . import coordinator as _coordinator
from .zone_ledger import as_float, as_int, reduce_zone_ledger

_LOGGER = logging.getLogger(__name__)
_SHADOW_TOTALS_KEY = "_zone_ledger_shadow"


def _close(left: Any, right: Any, *, tolerance: float) -> bool:
    """Compare optional numeric values while treating two missing values as equal."""
    first = as_float(left)
    second = as_float(right)
    if first is None or second is None:
        return first is None and second is None
    return abs(first - second) <= tolerance


def _rows_by_id(rows: Any) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        zone_id = as_int(row.get("id"))
        if zone_id is not None and zone_id > 0:
            result[zone_id] = row
    return result


def _zone_history_seed(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build migration/backfill facts without feeding legacy live percentages back in."""
    result: dict[str, dict[str, Any]] = {}
    for row in snapshot.get("zone_details") or []:
        if not isinstance(row, dict):
            continue
        zone_id = as_int(row.get("id"))
        if zone_id is None or zone_id <= 0:
            continue
        result[str(zone_id)] = {
            "id": zone_id,
            "name": row.get("name"),
            "area_m2": row.get("area_m2"),
            "vendor_percentage": row.get("vendor_percentage"),
            "percentage": row.get("percentage"),
            "last_started_at": row.get("last_started_at"),
            "last_mowed_at": row.get("last_mowed_at"),
            "last_completed_at": row.get("last_completed_at"),
            "last_completed_progress": row.get("last_completed_progress"),
            "last_completed_source": row.get("last_completed_source"),
            "last_completed_confirmation": row.get("last_completed_confirmation"),
            "last_completed_cycle_id": row.get("last_completed_cycle_id"),
        }
    return result


def _metric_diff(
    legacy: dict[str, Any],
    ledger: dict[str, Any],
    key: str,
    *,
    tolerance: float,
) -> dict[str, Any] | None:
    left = legacy.get(key)
    right = ledger.get(key)
    if _close(left, right, tolerance=tolerance):
        return None
    left_num = as_float(left)
    right_num = as_float(right)
    return {
        "legacy": left,
        "ledger": right,
        "delta": (
            round(right_num - left_num, 3)
            if left_num is not None and right_num is not None
            else None
        ),
    }


def build_shadow_diagnostics(
    *,
    legacy_rows: list[dict[str, Any]],
    legacy_totals: dict[str, Any],
    ledger_rows: list[dict[str, Any]],
    ledger_totals: dict[str, Any],
    ledger_task: dict[str, Any],
    ledger_state: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a compact, deterministic legacy-vs-ledger comparison."""
    legacy_by_id = _rows_by_id(legacy_rows)
    ledger_by_id = _rows_by_id(ledger_rows)
    zone_differences: list[dict[str, Any]] = []

    for zone_id in sorted(set(legacy_by_id) | set(ledger_by_id)):
        legacy = legacy_by_id.get(zone_id) or {}
        ledger = ledger_by_id.get(zone_id) or {}
        progress_match = _close(
            legacy.get("coverage_pct"), ledger.get("coverage_pct"), tolerance=0.1
        )
        area_match = _close(
            legacy.get("mowed_area_m2"), ledger.get("mowed_area_m2"), tolerance=0.05
        )
        if progress_match and area_match:
            continue
        legacy_pct = as_float(legacy.get("coverage_pct"))
        ledger_pct = as_float(ledger.get("coverage_pct"))
        legacy_area = as_float(legacy.get("mowed_area_m2"))
        ledger_area = as_float(ledger.get("mowed_area_m2"))
        zone_differences.append(
            {
                "zone_id": zone_id,
                "legacy_pct": legacy.get("coverage_pct"),
                "ledger_pct": ledger.get("coverage_pct"),
                "delta_pct": (
                    round(ledger_pct - legacy_pct, 2)
                    if legacy_pct is not None and ledger_pct is not None
                    else None
                ),
                "legacy_mowed_area_m2": legacy.get("mowed_area_m2"),
                "ledger_mowed_area_m2": ledger.get("mowed_area_m2"),
                "delta_mowed_area_m2": (
                    round(ledger_area - legacy_area, 2)
                    if legacy_area is not None and ledger_area is not None
                    else None
                ),
                "legacy_source": legacy.get("progress_source"),
                "ledger_source": ledger.get("progress_source"),
            }
        )

    metric_differences: dict[str, Any] = {}
    for key, tolerance in (
        ("map_area_m2", 0.05),
        ("map_mowed_area_m2", 0.05),
        ("map_coverage_pct", 0.1),
        ("task_area_m2", 0.05),
        ("task_mowed_area_m2", 0.05),
        ("task_progress_pct", 0.1),
    ):
        difference = _metric_diff(
            legacy_totals, ledger_totals, key, tolerance=tolerance
        )
        if difference is not None:
            metric_differences[key] = difference

    match = not zone_differences and not metric_differences
    return {
        "mode": "shadow",
        "public_owner": "legacy_zone_model",
        "ledger_revision": as_int(ledger_state.get("revision")) or 0,
        "match": match,
        "zone_match": not zone_differences,
        "metric_match": not metric_differences,
        "legacy": {
            "zone_count": len(legacy_by_id),
            "map_coverage_pct": legacy_totals.get("map_coverage_pct"),
            "map_mowed_area_m2": legacy_totals.get("map_mowed_area_m2"),
            "task_progress_pct": legacy_totals.get("task_progress_pct"),
            "task_mowed_area_m2": legacy_totals.get("task_mowed_area_m2"),
        },
        "ledger": {
            "zone_count": len(ledger_by_id),
            "map_coverage_pct": ledger_totals.get("map_coverage_pct"),
            "map_mowed_area_m2": ledger_totals.get("map_mowed_area_m2"),
            "task_progress_pct": ledger_totals.get("task_progress_pct"),
            "task_mowed_area_m2": ledger_totals.get("task_mowed_area_m2"),
            "task": deepcopy(ledger_task),
        },
        "zone_differences": zone_differences,
        "metric_differences": metric_differences,
        "recent_events": deepcopy(events[-8:]),
    }


def _run_shadow(owner: Any, snapshot: dict[str, Any]) -> None:
    """Reduce and compare one completed legacy snapshot without changing public values."""
    legacy_rows = snapshot.get("zone_states")
    legacy_totals = snapshot.get("totals")
    if not isinstance(legacy_rows, list) or not isinstance(legacy_totals, dict):
        return

    map_payload = snapshot.get("map")
    map_zones = map_payload.get("zones") if isinstance(map_payload, dict) else []
    active_session = getattr(getattr(owner, "history", None), "active_session", None)
    if not isinstance(active_session, dict):
        active_session = None

    previous_state = getattr(owner, "_zone_ledger_shadow_state", None)
    snapshot["coverage_observation_id"] = (getattr(owner, "_endpoint_status", {}).get("path_info_time") or {}).get("last_success_mono")
    state, rows, totals, task, events = reduce_zone_ledger(
        previous_state,
        snapshot=snapshot,
        map_zones=[dict(row) for row in map_zones or [] if isinstance(row, dict)],
        zone_details=[
            dict(row) for row in snapshot.get("zone_details") or [] if isinstance(row, dict)
        ],
        zone_history=_zone_history_seed(snapshot),
        active_session=active_session,
        observed_at_ms=int(time.time() * 1000),
    )
    owner._zone_ledger_shadow_state = state  # noqa: SLF001
    accept = getattr(owner, "_accept_vendor_observations", None)
    if accept is not None:
        accept(snapshot)
    diagnostics = build_shadow_diagnostics(
        legacy_rows=legacy_rows,
        legacy_totals=legacy_totals,
        ledger_rows=rows,
        ledger_totals=totals,
        ledger_task=task,
        ledger_state=state,
        events=events,
    )
    owner._zone_ledger_shadow_diagnostics = diagnostics  # noqa: SLF001
    diagnostics["cycle_owner"] = "ZoneLedger"
    diagnostics["trail_owner"] = "VendorTrailStore"
    snapshot["zone_ledger_shadow"] = diagnostics
    # Download diagnostics already includes ``totals``. Keeping the comparison
    # beneath a clearly private key makes the first shadow beta observable
    # without changing any sensor state or replacing the public totals values.
    legacy_totals[_SHADOW_TOTALS_KEY] = diagnostics


def install_zone_ledger_shadow_semantics() -> None:
    """Install ZoneLedger after the final legacy numeric semantics wrapper."""
    coordinator_cls = _coordinator.NavimowCoordinator
    if getattr(coordinator_cls, "_zone_ledger_shadow_semantics_installed", False):
        return

    original_refresh_zone_model = coordinator_cls._refresh_zone_model

    def refresh_zone_model(self: Any, snapshot: dict[str, Any]) -> None:
        original_refresh_zone_model(self, snapshot)
        try:
            _run_shadow(self, snapshot)
        except Exception as err:  # noqa: BLE001
            # Shadow mode must never alter availability or the public model.
            diagnostics = {
                "mode": "shadow",
                "public_owner": "legacy_zone_model",
                "match": None,
                "error": f"{type(err).__name__}: {err}",
            }
            self._zone_ledger_shadow_diagnostics = diagnostics  # noqa: SLF001
            snapshot["zone_ledger_shadow"] = diagnostics
            totals = snapshot.get("totals")
            if isinstance(totals, dict):
                totals[_SHADOW_TOTALS_KEY] = diagnostics
            _LOGGER.debug("ZoneLedger shadow reduction failed", exc_info=True)

    coordinator_cls._refresh_zone_model = refresh_zone_model
    coordinator_cls._zone_ledger_shadow_semantics_installed = True
