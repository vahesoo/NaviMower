"""Resume an existing vendor-retained Navimow mowing task."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from .const import ACTIVITY_MOWING


def _command_number(value: Any) -> str | None:
    """Extract a vendor command number without retaining the raw response."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text or None
    if isinstance(value, dict):
        for key in (
            "cmd_num",
            "cmdNum",
            "command_num",
            "commandNum",
            "command_number",
            "commandNumber",
        ):
            if key in value:
                found = _command_number(value.get(key))
                if found is not None:
                    return found
        for nested in value.values():
            if isinstance(nested, (dict, list, tuple)):
                found = _command_number(nested)
                if found is not None:
                    return found
    if isinstance(value, (list, tuple)):
        for nested in value:
            found = _command_number(nested)
            if found is not None:
                return found
    return None


def resume_command_diagnostics(coordinator) -> dict[str, Any] | None:
    """Return the latest in-memory resume trace for Download diagnostics."""
    trace = getattr(coordinator, "_last_resume_command", None)
    return deepcopy(trace) if isinstance(trace, dict) else None


async def async_resume_task(coordinator, *, source: str) -> Any:
    """Send the vendor Resume command without selecting zones or resetting progress.

    This is deliberately separate from ``navimower.mow(reset=False)``. Resume
    uses the retained vendor task through ``c:behavior`` type 3; it does not send
    a new ``s:mower`` zone command, does not select zones and does not start a
    new Navimower mowing cycle.
    """
    data = coordinator.data or {}
    trace: dict[str, Any] = {
        "source": source,
        "requested_at": datetime.now(UTC).isoformat(),
        "request": "c:behavior type=3",
        "zones_sent": False,
        "progress_reset_requested": False,
        "state_code_before": data.get("state_code"),
        "activity_before": data.get("activity"),
        "docked_before": data.get("docked"),
        "task_progress_before": data.get("mowing_progress"),
        "task_progress_source_before": data.get("mowing_progress_source"),
        "task_mowed_area_before": data.get("session_area"),
        "request_accepted": False,
    }
    coordinator._last_resume_command = trace
    coordinator.set_pending_activity(ACTIVITY_MOWING)

    try:
        result = await coordinator.async_send(
            coordinator.client.resume,
            coordinator.sn,
        )
    except Exception as err:
        trace["error"] = f"{type(err).__name__}: {err}"
        coordinator.clear_pending_activity()
        raise

    trace["request_accepted"] = True
    trace["command_number"] = _command_number(result)
    return result
