"""Read-only tracking for Navimow mower command responses.

A successful send request is not proof of physical mower motion. Track vendor
command-number statuses separately from public mower activity, without retries
of the original control command or any changes to gate/navigation logic.
"""
from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from .setting_write import (
    COMMAND_STATUS_LABELS,
    _command_number,
    _command_status_state,
)

CONTROL_METHODS = frozenset({"mow_zones", "pause", "dock", "resume"})
CONTROL_COMMAND_HISTORY_LIMIT = 12
CONTROL_COMMAND_MAX_POLLS = 4  # Maximum concurrent command monitors.
# Absolute deadlines after the vendor send acknowledgement, in seconds.
CONTROL_COMMAND_STATUS_POLL_AT_SECONDS = (1.0, 4.0, 10.0, 15.0, 25.0, 40.0, 60.0)
CONTROL_COMMAND_REFRESH_AT_SECONDS = frozenset({4.0, 15.0})

# Values are deliberately taken from the vendor's *actual* reports, never
# from the optimistic HA activity state or a cached pre-command value.
_MQTT_EXPECTED = {
    "mow_zones": frozenset({4}),
    "resume": frozenset({4}),
    "dock": frozenset({5, 2}),
    # MQTT vehicleState=3 means paused OR idle; never treat it as proof of Pause.
    "pause": frozenset(),
}
_PRIVATE_EXPECTED = {
    "mow_zones": frozenset({"0210", "0212"}),
    "resume": frozenset({"0210", "0212"}),
    "pause": frozenset({"0211"}),
    "dock": frozenset({"0220", "0101", "0102"}),
}

_TERMINAL = frozenset({"3", "4", "5", "9"})
_OUTCOMES = {
    "0": "pending",
    "1": "pending",
    "2": "pending",
    "3": "answered",
    "4": "expired",
    "5": "discarded",
    "9": "no_response",
}


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


def _reported_state(owner: Any) -> dict[str, Any]:
    """Cached vendor evidence only; never use optimistic HA activity as proof."""
    raw = getattr(owner, "_raw_cache", None)
    index2 = raw.get("index2") if isinstance(raw, dict) else None
    code = index2.get("vehicle_state") if isinstance(index2, dict) else None
    fresh_mqtt = getattr(owner, "_fresh_mqtt_vehicle_state", None)
    mqtt = fresh_mqtt() if callable(fresh_mqtt) else None
    return {
        "private_state_code": str(code) if code is not None else None,
        "mqtt_vehicle_state": mqtt if isinstance(mqtt, int) else None,
        "mqtt_named_state": str(getattr(owner, "_mqtt_named_state", "") or "") or None,
    }


def capture_control_command_context(
    owner: Any, func: Any, args: tuple[Any, ...], *, source: str | None = None,
) -> dict[str, Any] | None:
    """Snapshot safe command intent and pre-send vendor state before the executor.

    This deliberately contains no command number, token, serial, raw request,
    location or MQTT payload. `navimower.mow` also serves the Map Card's Mow UI.
    """
    method = str(getattr(func, "__name__", "") or "")
    if method not in CONTROL_METHODS:
        return None
    trace = None
    if method == "mow_zones":
        trace = getattr(owner, "_last_mow_command_trace", None)
    elif method == "resume":
        trace = getattr(owner, "_last_resume_command", None)
    trace = trace if isinstance(trace, dict) else {}
    origin = str(source or trace.get("source") or f"client.{method}")
    before = _reported_state(owner)
    trace_before = trace.get("state_before")
    if isinstance(trace_before, dict):
        before["state_code"] = trace_before.get("state_code")
        before["activity"] = trace_before.get("activity")
    elif method == "resume":
        before["state_code"] = trace.get("state_code_before")
        before["activity"] = trace.get("activity_before")
    else:
        data = getattr(owner, "data", None)
        before["state_code"] = data.get("state_code") if isinstance(data, dict) else None
        # Activity was already set optimistically by some callers; exclude it.
        before["activity"] = None

    if method == "mow_zones":
        setup = trace.get("partition_setup")
        if setup is None and args:
            setup = args[-1]
        try:
            setup = int(setup) if setup is not None else None
        except (ValueError, TypeError):
            setup = None
        request = {
            "command_family": "mow_zones",
            "zone_ids": [int(z) for z in (trace.get("resolved_zone_ids") or [])][:48],
            "reset": bool(trace.get("reset")) if "reset" in trace else bool(
                setup is not None and setup & 0x20
            ),
            "ordered": bool(trace.get("ordered")) if "ordered" in trace else bool(
                setup is not None and setup & 0x02
            ),
            "partition_setup": setup,
        }
    else:
        request = {
            "command_family": "behavior",
            "behavior_type": {"pause": 1, "dock": 2, "resume": 3}[method],
        }
    return {
        "command_source": origin,
        "request": request,
        "state_before": before,
        # Internal correlation only; never include this field in diagnostics.
        "_trace_token": trace.get("started_at_utc") if method == "mow_zones" else None,
    }


def _state(owner: Any) -> dict[str, Any]:
    state = getattr(owner, "_control_command_outcome_state", None)
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        state = {"schema_version": 1, "next_sequence": 0, "recent": []}
        owner._control_command_outcome_state = state
    return state


def control_command_snapshot(owner: Any) -> dict[str, Any]:
    """Only bounded status metadata; never export command IDs or payloads."""
    state = getattr(owner, "_control_command_outcome_state", None)
    if not isinstance(state, dict):
        return {"schema_version": 1, "latest": None, "recent_commands": []}
    recent = deepcopy(state.get("recent", [])[-CONTROL_COMMAND_HISTORY_LIMIT:])
    for entry in recent:
        entry.pop("_trace_token", None)
    return {
        "schema_version": 1,
        "latest": deepcopy(recent[-1]) if recent else None,
        "recent_commands": recent,
    }


def _new_row(owner: Any, method: str, *, acknowledged: bool, has_number: bool, context: dict[str, Any] | None = None) -> dict[str, Any]:
    store = _state(owner)
    store["next_sequence"] += 1
    row: dict[str, Any] = {
        "sequence": store["next_sequence"],
        "command": method,
        "sent_at_utc": _now_utc(),
        "send_acknowledged": acknowledged,
        "command_number_received": has_number,
        "vendor_status": None,
        "status_label": None,
        "outcome": "pending" if has_number else "no_command_number",
        "last_checked_at_utc": None,
        "query_count": 0,
        "observations": [],
        "physical_confirmation": "pending" if acknowledged else "not_checked",
        "physical_source": None,
        "physical_delay_s": None,
        "physical_observed_at_utc": None,
        "state_refresh_count": 0,
        "state_refresh_endpoints": [],
        "command_source": (context or {}).get("command_source") or f"client.{method}",
        "request": deepcopy((context or {}).get("request") or {}),
        "state_before": deepcopy((context or {}).get("state_before")),
        "observed_state": None,
        "state_after_monitor": None,
    }
    if context and context.get("_trace_token") is not None:
        # An internal token is kept in memory but excluded by the public snapshot.
        row["_trace_token"] = context["_trace_token"]
    store["recent"].append(row)
    del store["recent"][:-CONTROL_COMMAND_HISTORY_LIMIT]
    return row


def _observe_response(row: dict[str, Any], response: Any) -> bool:
    """Update the same row; return True only for terminal vendor statuses."""
    _, code = _command_status_state(response)
    row["query_count"] += 1
    row["last_checked_at_utc"] = _now_utc()
    if code not in COMMAND_STATUS_LABELS:
        row["outcome"] = "unknown_status" if response else "empty_response"
        return False
    row["vendor_status"] = code
    row["status_label"] = COMMAND_STATUS_LABELS[code]
    row["outcome"] = _OUTCOMES[code]
    records = row["observations"]
    if not records or records[-1]["vendor_status"] != code:
        records.append({
            "vendor_status": code,
            "meaning": COMMAND_STATUS_LABELS[code],
            "at_utc": row["last_checked_at_utc"],
        })
        del records[:-CONTROL_COMMAND_HISTORY_LIMIT]
    return code in _TERMINAL


def record_control_command_error(owner: Any, func: Any, error: Exception, *, context: dict[str, Any] | None = None) -> None:
    """Record only the error type; never include credentials or vendor payloads."""
    method = str(getattr(func, "__name__", "") or "")
    if method not in CONTROL_METHODS:
        return
    row = _new_row(owner, method, acknowledged=False, has_number=False, context=context)
    row["outcome"] = "send_error"
    row["error_type"] = type(error).__name__


def _confirm_observed_state(owner: Any, row: dict[str, Any], sent_mono: float) -> bool:
    """Accept only a matching vendor state *received after* the command reply.

    This is evidence of the expected state, not evidence of command causality.
    In particular, neither the HA optimistic activity nor MQTT's ambiguous
    paused-or-idle vehicleState=3 may confirm a Pause.
    """
    method = row["command"]
    observations: list[tuple[str, float]] = []

    mqtt_stamp = getattr(owner, "_mqtt_state_last_update", None)
    if isinstance(mqtt_stamp, (int, float)) and mqtt_stamp >= sent_mono:
        fresh_state = getattr(owner, "_fresh_mqtt_vehicle_state", None)
        mqtt_state = fresh_state() if callable(fresh_state) else None
        if mqtt_state in _MQTT_EXPECTED[method]:
            observations.append(("mqtt_vehicle_state", float(mqtt_stamp)))

    named_stamp = getattr(owner, "_mqtt_named_state_last_update", None)
    if method == "pause" and isinstance(named_stamp, (int, float)) and named_stamp >= sent_mono:
        named_state = str(getattr(owner, "_mqtt_named_state", "") or "").strip().casefold()
        if named_state == "ispaused":
            observations.append(("mqtt_named_state", float(named_stamp)))

    statuses = getattr(owner, "_endpoint_status", None)
    cloud_status = statuses.get("index2") if isinstance(statuses, dict) else None
    cloud_stamp = cloud_status.get("last_success_mono") if isinstance(cloud_status, dict) else None
    if isinstance(cloud_stamp, (int, float)) and cloud_stamp >= sent_mono:
        raw_cache = getattr(owner, "_raw_cache", None)
        index2 = raw_cache.get("index2") if isinstance(raw_cache, dict) else None
        raw_state = str(index2.get("vehicle_state") or "") if isinstance(index2, dict) else ""
        if raw_state in _PRIVATE_EXPECTED[method]:
            observations.append(("private_index2", float(cloud_stamp)))

    if not observations:
        return False

    # Prefer a freshly received official MQTT state if both sources agree.
    source, received_mono = observations[0]
    row["physical_confirmation"] = "state_observed"
    row["physical_source"] = source
    row["physical_delay_s"] = round(max(0.0, received_mono - sent_mono), 2)
    row["physical_observed_at_utc"] = _now_utc()
    row["observed_state"] = _reported_state(owner)
    return True


def _mark_control_state_endpoints_due(owner: Any, method: str) -> list[str]:
    """Invalidate only relevant endpoint TTLs; do not clear last-good data."""
    keys = ["index2"]
    if method in {"mow_zones", "resume", "dock"}:
        fresh_position = getattr(owner, "_fresh_mqtt_position", None)
        if not callable(fresh_position) or fresh_position() is None:
            keys.append("location")

    statuses = getattr(owner, "_endpoint_status", None)
    if isinstance(statuses, dict):
        for key in keys:
            status = statuses.get(key)
            if isinstance(status, dict):
                status["last_attempt_mono"] = None
    return keys


def record_control_command_send(owner: Any, func: Any, response: Any, *, context: dict[str, Any] | None = None) -> None:
    """Monitor vendor feedback and physical-state evidence without blocking HA.

    The command is never replayed. At most two targeted, non-blocking private
    refreshes are requested, and both are skipped once fresh MQTT/private
    evidence already matches the expected state.
    """
    method = str(getattr(func, "__name__", "") or "")
    if method not in CONTROL_METHODS:
        return

    number = _command_number(response)
    row = _new_row(owner, method, acknowledged=True, has_number=number is not None, context=context)
    query = getattr(getattr(owner, "client", None), "command_status", None)
    can_query = number is not None and callable(query)
    if number is not None and not callable(query):
        row["outcome"] = "unsupported"

    tasks = getattr(owner, "_control_command_outcome_tasks", None)
    if not isinstance(tasks, dict):
        tasks = {}
        owner._control_command_outcome_tasks = tasks
    if len(tasks) >= CONTROL_COMMAND_MAX_POLLS:
        oldest = min(tasks)
        old_task = tasks.pop(oldest)
        if not old_task.done():
            old_task.cancel()
        for previous in _state(owner)["recent"]:
            if previous["sequence"] == oldest:
                if previous["outcome"] in {"pending", "unknown_status", "empty_response"}:
                    previous["outcome"] = "superseded"
                if previous["physical_confirmation"] == "pending":
                    previous["physical_confirmation"] = "superseded"

    sequence = row["sequence"]
    sent_mono = time.monotonic()

    async def _monitor() -> None:
        last_error_type: str | None = None
        response_seen = False
        status_done = not can_query
        try:
            for deadline in CONTROL_COMMAND_STATUS_POLL_AT_SECONDS:
                await asyncio.sleep(max(0.0, sent_mono + deadline - time.monotonic()))
                if getattr(owner, "_shutdown_complete", False):
                    if not status_done:
                        row["outcome"] = "shutdown"
                    if row["physical_confirmation"] == "pending":
                        row["physical_confirmation"] = "shutdown"
                    return

                observed = _confirm_observed_state(owner, row, sent_mono)

                if not status_done:
                    try:
                        response_value = await owner.hass.async_add_executor_job(
                            query, owner.sn, number,
                        )
                    except Exception as err:  # read-only best-effort
                        last_error_type = type(err).__name__
                    else:
                        response_seen = response_seen or bool(response_value)
                        if _observe_response(row, response_value):
                            status_done = True

                if deadline in CONTROL_COMMAND_REFRESH_AT_SECONDS and not observed:
                    row["state_refresh_endpoints"] = _mark_control_state_endpoints_due(owner, method)
                    row["state_refresh_count"] += 1
                    try:
                        await owner.async_request_refresh()
                    except Exception as err:  # diagnostics must never fail a command
                        row["state_refresh_error_type"] = type(err).__name__
                    observed = _confirm_observed_state(owner, row, sent_mono)

                if status_done and observed:
                    return
                if status_done and deadline >= max(CONTROL_COMMAND_REFRESH_AT_SECONDS):
                    break

            if not status_done:
                if response_seen:
                    row["outcome"] = "poll_timeout"
                elif last_error_type is not None:
                    row["outcome"] = "query_error"
                    row["error_type"] = last_error_type
                else:
                    row["outcome"] = "empty_response"
                row["last_checked_at_utc"] = _now_utc()
            if row["physical_confirmation"] == "pending":
                row["physical_confirmation"] = "unconfirmed"
        except asyncio.CancelledError:
            if row["outcome"] in {"pending", "unknown_status", "empty_response"}:
                row["outcome"] = "cancelled"
            if row["physical_confirmation"] == "pending":
                row["physical_confirmation"] = "cancelled"
            raise
        except Exception as err:
            # Only diagnostic monitoring failed, never the already-sent command.
            if row["outcome"] in {"pending", "unknown_status", "empty_response"}:
                row["outcome"] = "monitor_error"
            row["monitor_error_type"] = type(err).__name__
            if row["physical_confirmation"] == "pending":
                row["physical_confirmation"] = "unconfirmed"
        finally:
            row["state_after_monitor"] = _reported_state(owner)
            if method == "mow_zones":
                notify = getattr(owner, "record_mow_command_verification", None)
                if callable(notify):
                    try:
                        notify(
                            row.get("_trace_token"),
                            physical_confirmation=row["physical_confirmation"],
                            vendor_outcome=row["outcome"],
                        )
                    except Exception as err:
                        row["verification_record_error_type"] = type(err).__name__
            if tasks.get(sequence) is asyncio.current_task():
                tasks.pop(sequence, None)

    tasks[sequence] = owner.hass.async_create_task(
        _monitor(), f"Navimower command monitor {owner.entry.entry_id} {sequence}",
    )


def cancel_control_command_tracking(owner: Any) -> None:
    """Cancel only read-only status-polling tasks during integration shutdown."""
    tasks = getattr(owner, "_control_command_outcome_tasks", None)
    if not isinstance(tasks, dict):
        return
    for task in tasks.values():
        if not task.done():
            task.cancel()
    tasks.clear()
