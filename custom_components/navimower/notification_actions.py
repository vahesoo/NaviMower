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


async def _async_vendor_calls_and_refresh(
    coordinator: Any,
    path: str,
    payloads: list[dict[str, Any]],
) -> list[Any]:
    """Run explicit vendor notification calls and refresh the Device feed once."""
    results: list[Any] = []
    for payload in payloads:
        results.append(
            await coordinator.hass.async_add_executor_job(
                coordinator.client.call,
                path,
                payload,
            )
        )

    # A successful call may have refreshed the private-cloud auth session.
    coordinator._persist_session()  # noqa: SLF001

    # Invalidate only after every requested vendor call succeeds so the refresh
    # cannot reuse a pre-command notification snapshot because of the 60s TTL.
    _invalidate_notification_poll(coordinator)
    await coordinator.async_request_refresh()
    return results


async def _async_vendor_call_and_refresh(
    coordinator: Any,
    path: str,
    payload: dict[str, Any],
) -> Any:
    """Run one encrypted vendor call and immediately refresh the Device feed."""
    return (await _async_vendor_calls_and_refresh(coordinator, path, [payload]))[0]


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

    # A visible canonical row can represent several repeated vendor rows. Keep
    # the frontend contract simple: it sends the representative message_id and
    # the integration marks every retained member of that short-window group.
    group_map = getattr(coordinator, "_notification_group_members", None)
    grouped_ids = (
        list(group_map.get(message_id) or [])
        if isinstance(group_map, dict)
        else []
    )
    member_ids: list[str] = []
    for candidate in grouped_ids or [message_id]:
        candidate = str(candidate or "").strip()
        if candidate and candidate not in member_ids:
            member_ids.append(candidate)

    # Vendor message IDs continue through the app's encrypted detail route.
    # Record one privacy-safe trace for the explicit user action, then refresh
    # the Device feed once after all group members have been opened.
    trace: dict[str, Any] = {
        "requested_at": datetime.now(UTC).isoformat(),
        "path": _NOTIFICATION_DETAIL_PATH,
        "message_id": message_id,
        "grouped_count": len(member_ids),
        "type": _DEVICE_MESSAGE_DETAIL_TYPE,
        "explicit_user_action": True,
        "request_succeeded": False,
    }
    coordinator._last_notification_detail_trace = trace  # noqa: SLF001
    payloads = [
        {
            "message_id": member_id,
            "type": _DEVICE_MESSAGE_DETAIL_TYPE,
            "vehicle_sn": str(coordinator.sn),
        }
        for member_id in member_ids
    ]
    try:
        results = await _async_vendor_calls_and_refresh(
            coordinator,
            _NOTIFICATION_DETAIL_PATH,
            payloads,
        )
    except Exception as err:
        trace["error"] = f"{type(err).__name__}: {err}"
        raise
    trace["request_succeeded"] = True
    trace["response_count"] = len(results)
    if len(results) == 1:
        trace["response"] = deepcopy(results[0])
        return results[0]
    return {
        "grouped": True,
        "marked_read": len(results),
        "message_id": message_id,
    }


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
