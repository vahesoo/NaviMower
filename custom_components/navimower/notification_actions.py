"""Read-state actions for vendor and Navimower-local notifications.

Vendor rows keep the official Navimow H5 mutation contracts and are refreshed
from the cloud after each explicit action. Navimower-generated rows use the
``navimower:`` message-id namespace and are marked read only in the local
persistent notification Store; they are never sent to a vendor message route.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from .notification_center import LOCAL_NOTIFICATION_PREFIX

_NOTIFICATION_MARK_ALL_PATH = "/mowerbot/user/message/clearBatchMessageRead"
_NOTIFICATION_DETAIL_PATH = "/mowerbot/user/message/getmessageDetailResp"
_DEVICE_MESSAGE_DETAIL_TYPE = 2


def _invalidate_notification_poll(coordinator: Any) -> None:
    """Make the next Device-feed refresh bypass the normal 60-second TTL."""
    coordinator._notification_last_attempt_mono = None  # noqa: SLF001


def notification_detail_diagnostics(coordinator: Any) -> dict[str, Any] | None:
    """Return the last explicit vendor notification-detail trace, if any."""
    trace = getattr(coordinator, "_last_notification_detail_trace", None)
    return deepcopy(trace) if isinstance(trace, dict) else None


async def _async_vendor_call_and_refresh(
    coordinator: Any,
    path: str,
    payload: dict[str, Any],
) -> Any:
    """Run one encrypted vendor call and immediately refresh the Device feed."""
    result = await coordinator.hass.async_add_executor_job(
        coordinator.client.call,
        path,
        payload,
    )
    # A successful call may have refreshed the private-cloud auth session.
    coordinator._persist_session()  # noqa: SLF001

    # Invalidate only after the vendor call succeeds so async_request_refresh()
    # cannot reuse a pre-command notification snapshot because of the 60s TTL.
    _invalidate_notification_poll(coordinator)
    await coordinator.async_request_refresh()
    return result


async def async_mark_notification_read(
    coordinator: Any,
    message_id: str,
) -> Any:
    """Mark one vendor or Navimower-local notification read."""
    message_id = str(message_id or "").strip()
    if not message_id:
        raise ValueError("message_id is required")

    if message_id.startswith(LOCAL_NOTIFICATION_PREFIX):
        center = getattr(coordinator, "notification_center", None)
        if center is None:
            raise ValueError("Navimower local notification center is unavailable")
        changed = await center.async_mark_read(message_id)
        if not changed:
            raise ValueError("Navimower local notification is no longer retained")
        return {"origin": "navimower", "message_id": message_id, "read": True}

    # Vendor message IDs continue through the app's encrypted detail route. Beta11
    # records the response from this already-explicit user action in memory so a
    # later Download diagnostics can inspect the real detail contract without
    # issuing a hidden read/detail request itself.
    trace: dict[str, Any] = {
        "requested_at": datetime.now(UTC).isoformat(),
        "path": _NOTIFICATION_DETAIL_PATH,
        "message_id": message_id,
        "type": _DEVICE_MESSAGE_DETAIL_TYPE,
        "explicit_user_action": True,
        "request_succeeded": False,
    }
    coordinator._last_notification_detail_trace = trace  # noqa: SLF001
    try:
        result = await _async_vendor_call_and_refresh(
            coordinator,
            _NOTIFICATION_DETAIL_PATH,
            {
                "message_id": message_id,
                "type": _DEVICE_MESSAGE_DETAIL_TYPE,
                "vehicle_sn": str(coordinator.sn),
            },
        )
    except Exception as err:
        trace["error"] = f"{type(err).__name__}: {err}"
        raise
    trace["request_succeeded"] = True
    trace["response"] = deepcopy(result)
    return result


async def async_mark_all_notifications_read(coordinator: Any) -> Any:
    """Mark all retained local rows plus all vendor Device notifications read."""
    center = getattr(coordinator, "notification_center", None)
    local_changed = await center.async_mark_all_read() if center is not None else 0

    # H5 uses the same endpoint with searchMessageStatus=true as a preflight
    # check. The actual vendor Mark all as read action sends false.
    vendor_result = await _async_vendor_call_and_refresh(
        coordinator,
        _NOTIFICATION_MARK_ALL_PATH,
        {
            "searchMessageStatus": False,
            "vehicle_sn": str(coordinator.sn),
        },
    )
    return {
        "local_marked_read": local_changed,
        "vendor_result": vendor_result,
    }
