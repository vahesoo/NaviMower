"""Read-only tracking for Navimow mower command responses.

A successful send request is not proof of physical mower motion. Track vendor
command-number statuses separately from public mower activity, without retries
of the original control command or any changes to gate/navigation logic.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from .setting_write import (
    COMMAND_STATUS_LABELS,
    SETTING_COMMAND_STATUS_RETRY_DELAYS_SECONDS,
    _command_number,
    _command_status_state,
)

CONTROL_METHODS = frozenset({"mow_zones", "pause", "dock", "resume"})
CONTROL_COMMAND_HISTORY_LIMIT = 12
CONTROL_COMMAND_MAX_POLLS = 4

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
        return {"schema_version": 1, "latest": None, "recent": []}
    recent = deepcopy(state.get("recent", [])[-CONTROL_COMMAND_HISTORY_LIMIT:])
    return {
        "schema_version": 1,
        "latest": deepcopy(recent[-1]) if recent else None,
        "recent": recent,
    }


def _new_row(owner: Any, method: str, *, acknowledged: bool, has_number: bool) -> dict[str, Any]:
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
    }
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


def record_control_command_error(owner: Any, func: Any, error: Exception) -> None:
    """Record only the error type; never include credentials or vendor payloads."""
    method = str(getattr(func, "__name__", "") or "")
    if method not in CONTROL_METHODS:
        return
    row = _new_row(owner, method, acknowledged=False, has_number=False)
    row["outcome"] = "send_error"
    row["error_type"] = type(error).__name__


def record_control_command_send(owner: Any, func: Any, response: Any) -> None:
    """Schedule bounded, non-blocking status reads for accepted control sends."""
    method = str(getattr(func, "__name__", "") or "")
    if method not in CONTROL_METHODS:
        return

    number = _command_number(response)
    row = _new_row(owner, method, acknowledged=True, has_number=number is not None)
    if number is None:
        return

    query = getattr(getattr(owner, "client", None), "command_status", None)
    if not callable(query):
        row["outcome"] = "unsupported"
        return

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
            if previous["sequence"] == oldest and previous["outcome"] in {
                "pending", "unknown_status", "empty_response",
            }:
                previous["outcome"] = "superseded"

    sequence = row["sequence"]

    async def _check() -> None:
        last_error_type: str | None = None
        response_seen = False
        try:
            for delay in SETTING_COMMAND_STATUS_RETRY_DELAYS_SECONDS:
                await asyncio.sleep(delay)
                if getattr(owner, "_shutdown_complete", False):
                    row["outcome"] = "shutdown"
                    return
                try:
                    response_value = await owner.hass.async_add_executor_job(
                        query, owner.sn, number,
                    )
                except Exception as err:  # read-only, best-effort check
                    last_error_type = type(err).__name__
                    continue
                response_seen = response_seen or bool(response_value)
                if _observe_response(row, response_value):
                    return

            if response_seen:
                row["outcome"] = "poll_timeout"
            elif last_error_type is not None:
                row["outcome"] = "query_error"
                row["error_type"] = last_error_type
            else:
                row["outcome"] = "empty_response"
            row["last_checked_at_utc"] = _now_utc()
        except asyncio.CancelledError:
            if row["outcome"] in {"pending", "unknown_status", "empty_response"}:
                row["outcome"] = "cancelled"
            raise
        finally:
            if tasks.get(sequence) is asyncio.current_task():
                tasks.pop(sequence, None)

    tasks[sequence] = owner.hass.async_create_task(
        _check(), f"Navimower command status {owner.entry.entry_id} {sequence}",
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
