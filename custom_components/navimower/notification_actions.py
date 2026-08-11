"""Explicit notification read-state actions recovered from the Navimow H5 app.

These helpers are intentionally separate from the normal read-only notification
poller.  They run only after an explicit Home Assistant service call and never
optimistically rewrite the cached `read` flags: after the vendor request the
Device feed is fetched again and remains authoritative.
"""
from __future__ import annotations

from typing import Any

_NOTIFICATION_MARK_ALL_PATH = "/mowerbot/user/message/clearBatchMessageRead"
_NOTIFICATION_DETAIL_PATH = "/mowerbot/user/message/getmessageDetailResp"
_DEVICE_MESSAGE_DETAIL_TYPE = 2


def _invalidate_notification_poll(coordinator: Any) -> None:
    """Make the next Device-feed refresh bypass the normal 60-second TTL."""
    coordinator._beta26_notification_last_attempt_mono = None  # noqa: SLF001


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
    """Open one Device message through the app's detail route.

    The official Message Center marks a row read locally and then opens this
    encrypted detail route.  Beta2 deliberately does not set the Home Assistant
    cache to read=True itself; the following vehicleMessageListField refresh must
    confirm the server-side read state.
    """
    message_id = str(message_id or "").strip()
    if not message_id:
        raise ValueError("message_id is required")
    return await _async_vendor_call_and_refresh(
        coordinator,
        _NOTIFICATION_DETAIL_PATH,
        {
            "message_id": message_id,
            "type": _DEVICE_MESSAGE_DETAIL_TYPE,
            "vehicle_sn": str(coordinator.sn),
        },
    )


async def async_mark_all_notifications_read(coordinator: Any) -> Any:
    """Mark all Device notifications read for this account/mower.

    H5 uses the same endpoint with searchMessageStatus=true as a preflight check.
    The actual Mark all as read action sends searchMessageStatus=false.
    """
    return await _async_vendor_call_and_refresh(
        coordinator,
        _NOTIFICATION_MARK_ALL_PATH,
        {
            "searchMessageStatus": False,
            "vehicle_sn": str(coordinator.sn),
        },
    )
