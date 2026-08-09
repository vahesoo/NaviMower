"""Short-lived state-transition capture for vendor error/event research.

This module is diagnostic-only.  It hooks the coordinator's existing MQTT state
ingestion methods and, when a named or numeric MQTT state changes, takes a small
sequence of read-only private-cloud snapshots.  The goal is to catch transient
vendor codes/flags that can disappear between normal coordinator polls.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
import re
from typing import Any

from .discovery import sanitize_discovery_value, structure_summary

_CAPTURE_LIMIT = 30
_PHASE_DELAYS = (0.0, 1.0, 5.0)
_CODE_TOKEN_RE = re.compile(r"(?i)(?<![0-9a-f])([0-9a-f]{3,8})(?![0-9a-f])")
_SIGNAL_KEYS = {
    "code",
    "error",
    "errorcode",
    "error_code",
    "fault",
    "faultcode",
    "fault_code",
    "event",
    "eventcode",
    "event_code",
    "reason",
    "state",
    "status",
    "type",
    "vehiclestate",
    "vehicle_state",
    "warningcode",
    "warning_code",
    "alarmcode",
    "alarm_code",
}


def _compact_key(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_")


def _code_candidates(value: Any) -> list[str]:
    """Return bounded vendor-looking code tokens from signal-like fields."""
    found: set[str] = set()

    def walk(current: Any, key: str = "", depth: int = 0) -> None:
        if depth >= 8:
            return
        if isinstance(current, dict):
            for index, (raw_key, child) in enumerate(current.items()):
                if index >= 80:
                    break
                walk(child, str(raw_key), depth + 1)
            return
        if isinstance(current, (list, tuple)):
            for child in current[:80]:
                walk(child, key, depth + 1)
            return
        normalized = _compact_key(key)
        compact = normalized.replace("_", "")
        signal = (
            normalized in _SIGNAL_KEYS
            or compact in {item.replace("_", "") for item in _SIGNAL_KEYS}
            or any(marker in compact for marker in ("error", "fault", "event", "code", "alarm", "warning"))
        )
        if not signal or current is None or isinstance(current, bool):
            return
        text = str(current).upper()
        for match in _CODE_TOKEN_RE.finditer(text):
            token = match.group(1).upper()
            if any(ch.isdigit() for ch in token):
                found.add(token)

    walk(value)
    return sorted(found)[:128]


def _auth_item(rows: Any, sn: str) -> dict[str, Any]:
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if isinstance(row, dict) and str(row.get("vehicle_sn") or "") == str(sn):
            return row
    return {}


def _selected_snapshot(index2: Any, auth_list: Any, location: Any, sn: str) -> dict[str, Any]:
    """Keep only state/error fields useful for transition research."""
    index = index2 if isinstance(index2, dict) else {}
    auth = _auth_item(auth_list, sn)
    loc = location if isinstance(location, dict) else {}

    selected = {
        "index2": {
            "vehicle_state": index.get("vehicle_state"),
            "error_data": index.get("error_data"),
            "boolState": index.get("boolState"),
            "permanent_data": index.get("permanent_data"),
            "map_work_position": index.get("map_work_position"),
            "partitionIdList": index.get("partitionIdList"),
            "network_status": index.get("network_status"),
            "soc": index.get("soc"),
            "vehicle_info_update_time": index.get("vehicle_info_update_time"),
            "vehicleSettingUpdateTime": index.get("vehicleSettingUpdateTime"),
            "camerabox": index.get("camerabox"),
        },
        "auth_list_item": {
            "vehicle_state": auth.get("vehicle_state"),
            "m_task_status": auth.get("m_task_status"),
            "network_status": auth.get("network_status"),
            "networkType": auth.get("networkType"),
            "soc": auth.get("soc"),
            "lastSwitchTime": auth.get("lastSwitchTime"),
            "auth_time": auth.get("auth_time"),
        },
        "location": {
            "report_time": loc.get("report_time"),
            "mowing_percentage": loc.get("mowing_percentage"),
            "map_work_position": loc.get("map_work_position"),
            "path_id": loc.get("path_id"),
            "subtotal_area": loc.get("subtotal_area"),
            "mowing_week_area": loc.get("mowing_week_area"),
        },
    }
    return sanitize_discovery_value(selected)


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, dict):
                out.update(_flatten(child, path))
            else:
                out[path] = child
        return out
    out[prefix or "$" ] = value
    return out


def _diff(before: Any, after: Any) -> dict[str, dict[str, Any]]:
    left = _flatten(before)
    right = _flatten(after)
    changed: dict[str, dict[str, Any]] = {}
    for key in sorted(set(left) | set(right)):
        if left.get(key) != right.get(key):
            changed[key] = {"before": left.get(key), "after": right.get(key)}
    return changed


def _boolstate_delta(before: Any, after: Any) -> dict[str, Any] | None:
    if before in (None, "") or after in (None, ""):
        return None
    try:
        old = int(str(before), 16)
        new = int(str(after), 16)
    except (TypeError, ValueError):
        return None
    xor = old ^ new
    width = max(len(str(before)), len(str(after)), 8)
    return {
        "before": str(before),
        "after": str(after),
        "xor_hex": f"{xor:0{width}X}",
        "set_bits": [bit for bit in range(max(1, xor.bit_length())) if (xor & (1 << bit)) and (new & (1 << bit))],
        "cleared_bits": [bit for bit in range(max(1, xor.bit_length())) if (xor & (1 << bit)) and not (new & (1 << bit))],
    }


class StateTransitionCapture:
    """Collect read-only private-cloud snapshots around MQTT state transitions."""

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self._records: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._sequence = 0

    def _cached_baseline(self) -> dict[str, Any]:
        raw = getattr(self.coordinator, "_raw_cache", {}) or {}
        return _selected_snapshot(
            raw.get("index2"),
            raw.get("auth_list"),
            raw.get("location"),
            self.coordinator.sn,
        )

    def trigger(self, *, source: str, before: Any, after: Any, mqtt_payload: Any = None) -> None:
        if before == after and before is not None:
            return
        self._sequence += 1
        sequence = self._sequence
        baseline = self._cached_baseline()
        event = {
            "sequence": sequence,
            "trigger_utc": datetime.now(UTC).isoformat(),
            "source": str(source),
            "before": before,
            "after": after,
            "mqtt_payload": sanitize_discovery_value(mqtt_payload),
            "baseline": baseline,
            "captures": [],
        }
        self._records.append(event)
        del self._records[:-_CAPTURE_LIMIT]
        self.hass.async_create_task(
            self._capture_sequence(event),
            f"Navimower state transition capture {self.coordinator.entry.entry_id} #{sequence}",
        )

    async def _capture_sequence(self, event: dict[str, Any]) -> None:
        previous = event.get("baseline") or {}
        elapsed = 0.0
        for delay in _PHASE_DELAYS:
            wait = max(0.0, delay - elapsed)
            elapsed = delay
            if wait:
                await asyncio.sleep(wait)
            if getattr(self.coordinator, "_shutdown_complete", False):
                return
            capture = await self._capture_phase(delay, previous)
            event["captures"].append(capture)
            previous = capture.get("snapshot") or previous

    async def _capture_phase(self, delay_s: float, previous: Any) -> dict[str, Any]:
        async with self._lock:
            raw, errors = await self.hass.async_add_executor_job(self._read_cloud_state)
        snapshot = _selected_snapshot(
            raw.get("index2"),
            raw.get("auth_list"),
            raw.get("location"),
            self.coordinator.sn,
        )
        old_bool = ((previous or {}).get("index2") or {}).get("boolState") if isinstance(previous, dict) else None
        new_bool = ((snapshot or {}).get("index2") or {}).get("boolState") if isinstance(snapshot, dict) else None
        summary = structure_summary(snapshot)
        return {
            "captured_utc": datetime.now(UTC).isoformat(),
            "delay_s": delay_s,
            "errors": errors,
            "snapshot": snapshot,
            "changed": _diff(previous, snapshot),
            "boolState_delta": _boolstate_delta(old_bool, new_bool),
            "code_candidates": sorted(
                set(_code_candidates(snapshot))
                | {
                    item.split("=", 1)[1]
                    for item in summary.get("observed_type_values", [])
                    if str(item).startswith("code_candidate=")
                }
            ),
        }

    def _read_cloud_state(self) -> tuple[dict[str, Any], dict[str, str]]:
        client = self.coordinator.client
        sn = self.coordinator.sn
        vtype = self.coordinator.vehicle_type
        values: dict[str, Any] = {}
        errors: dict[str, str] = {}
        calls = (
            ("index2", client.index2, (sn,)),
            ("auth_list", client.auth_list, ()),
            ("location", client.location, (sn, vtype)),
        )
        for name, func, args in calls:
            try:
                values[name] = func(*args)
            except Exception as err:  # noqa: BLE001 - diagnostics must survive failures.
                errors[name] = f"{type(err).__name__}: {err}"
                values[name] = None
        return values, errors

    def diagnostics(self) -> dict[str, Any]:
        return {
            "capture_limit": _CAPTURE_LIMIT,
            "phase_delays_s": list(_PHASE_DELAYS),
            "capture_count": len(self._records),
            "events": deepcopy(self._records),
        }


def _manager(coordinator: Any) -> StateTransitionCapture:
    manager = getattr(coordinator, "_state_transition_capture", None)
    if not isinstance(manager, StateTransitionCapture):
        manager = StateTransitionCapture(coordinator)
        coordinator._state_transition_capture = manager
    return manager


def install_state_transition_capture() -> None:
    """Patch coordinator ingestion once; kept isolated for easy beta removal."""
    from .coordinator import NavimowCoordinator

    if getattr(NavimowCoordinator, "_beta15_transition_capture_installed", False):
        return

    original_state = NavimowCoordinator.ingest_mqtt_state
    original_location = NavimowCoordinator.ingest_mqtt_location

    def ingest_mqtt_state(self: Any, state: dict[str, Any]) -> None:
        before = str(getattr(self, "_mqtt_named_state", "") or "") or None
        original_state(self, state)
        after = str(getattr(self, "_mqtt_named_state", "") or "") or None
        if after and before != after:
            _manager(self).trigger(
                source="mqtt_named_state",
                before=before,
                after=after,
                mqtt_payload=state,
            )

    def ingest_mqtt_location(self: Any, location: dict[str, Any]) -> None:
        old = getattr(self, "_mqtt_location", None) or {}
        before = {
            "vehicle_state": old.get("vehicle_state"),
            "action": old.get("action"),
        }
        original_location(self, location)
        new = getattr(self, "_mqtt_location", None) or {}
        after = {
            "vehicle_state": new.get("vehicle_state"),
            "action": new.get("action"),
        }
        if before != after and any(value is not None for value in after.values()):
            _manager(self).trigger(
                source="mqtt_numeric_state_action",
                before=before,
                after=after,
                mqtt_payload={
                    "vehicle_state": location.get("vehicle_state"),
                    "action": location.get("action"),
                    "type": location.get("type"),
                    "pose_time": location.get("pose_time"),
                },
            )

    def state_transition_diagnostics(self: Any) -> dict[str, Any]:
        manager = getattr(self, "_state_transition_capture", None)
        if isinstance(manager, StateTransitionCapture):
            return manager.diagnostics()
        return {
            "capture_limit": _CAPTURE_LIMIT,
            "phase_delays_s": list(_PHASE_DELAYS),
            "capture_count": 0,
            "events": [],
        }

    NavimowCoordinator.ingest_mqtt_state = ingest_mqtt_state
    NavimowCoordinator.ingest_mqtt_location = ingest_mqtt_location
    NavimowCoordinator.state_transition_diagnostics = state_transition_diagnostics
    NavimowCoordinator._beta15_transition_capture_installed = True
