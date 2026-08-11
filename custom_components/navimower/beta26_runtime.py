"""Vendor notification support introduced in beta26 and refined in beta29.

Beta26 initially used Navimow's separate vehicle message-history route. Beta28
then recovered the actual main app Notification -> Device feed contract from the
public H5 MessageCenter component. Beta29 switches the live sensor to that
read-only encrypted feed while keeping the old history method available only as
an inert compatibility/debug helper.

Main Device feed contract recovered from H5:

* POST ``/mowerbot/user/message/vehicleMessageListField`` through p:101,
* payload ``message_id`` + ``vehicle_sn`` + ``filter_state``,
* first page uses an empty ``message_id`` and ``filter_state=all``,
* response exposes ``vehicle_message_list`` and ``has_history_message``.

The integration polls at most once per minute, retains the last successful
response across transient failures, exposes a bounded recent list, and never
marks messages read or calls any read-state mutation endpoint.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import time
from typing import Any

from . import coordinator as _coordinator
from .api.client import NavimowCloudClient

# Main Notification -> Device feed used from beta29 onward.
_NOTIFICATION_PATH = "/mowerbot/user/message/vehicleMessageListField"
_NOTIFICATION_FILTER = "all"
_NOTIFICATION_TTL_SECONDS = 60
_NOTIFICATION_ATTR_HISTORY_LIMIT = 5

# Historical beta26 route kept as a callable compatibility/debug helper only.
# It is no longer used by normal polling or Download diagnostics from beta29.
_LEGACY_NOTIFICATION_PATH = "/mowerbot/user/message/get-vehicle-history-message"
_NOTIFICATION_PAGE_SIZE = 20


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bounded_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _first_present(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item.get(key) is not None:
            return item.get(key)
    return None


def _created_at(value: Any) -> str | None:
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return None
    if stamp > 10_000_000_000:
        stamp /= 1000.0
    try:
        return datetime.fromtimestamp(stamp, UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _normalize_item(item: Any) -> dict[str, Any] | None:
    """Normalize known/likely vendor field aliases without inventing values."""
    if not isinstance(item, dict):
        return None

    addtime = _first_present(
        item,
        "addtime",
        "add_time",
        "create_time",
        "createTime",
        "timestamp",
        "time",
    )
    return {
        "id": _first_present(item, "id", "message_record_id"),
        "message_id": _first_present(item, "message_id", "messageId"),
        "title": _bounded_text(
            _first_present(item, "title", "message_title", "messageTitle"),
            255,
        ),
        "content": _bounded_text(
            _first_present(item, "content", "message_content", "messageContent"),
            1200,
        ),
        "addtime": addtime,
        "created_at": _created_at(addtime),
        "read": _as_int(_first_present(item, "read", "is_read", "isRead")),
        "level": _as_int(_first_present(item, "level", "message_level")),
        "type": _first_present(item, "type", "message_type", "messageType"),
        "event_code": _first_present(
            item,
            "event_code",
            "eventCode",
            "message_code",
            "messageCode",
        ),
    }


def _normalize_response(value: Any) -> dict[str, Any]:
    response = value if isinstance(value, dict) else {}
    raw_messages = response.get("vehicle_message_list")
    source_key = "vehicle_message_list"
    if not isinstance(raw_messages, list):
        # Legacy fallback keeps persisted/cache data from older betas readable.
        raw_messages = response.get("list")
        source_key = "list"
    if not isinstance(raw_messages, list):
        raw_messages = []

    messages: list[dict[str, Any]] = []
    for item in raw_messages:
        normalized = _normalize_item(item)
        if normalized is not None:
            messages.append(normalized)

    return {
        "list": messages,
        "count": len(messages),
        "has_history_message": response.get("has_history_message"),
        "source_key": source_key,
        "next_message_id": (
            messages[-1].get("message_id") if messages else None
        ),
    }


def _decorate_snapshot(coordinator: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    result = dict(snapshot)
    cache = getattr(coordinator, "_beta26_notification_cache", None)
    normalized = _normalize_response(cache)
    messages = normalized["list"]
    latest = messages[0] if messages else {}
    last_success = getattr(coordinator, "_beta26_notification_last_success_mono", None)
    source_age = (
        max(0.0, time.monotonic() - float(last_success))
        if last_success is not None
        else None
    )
    result.update(
        {
            "notification_title": latest.get("title"),
            "notification_content": latest.get("content"),
            "notification_message_id": latest.get("message_id"),
            "notification_addtime": latest.get("addtime"),
            "notification_created_at": latest.get("created_at"),
            "notification_read": latest.get("read"),
            "notification_level": latest.get("level"),
            "notification_type": latest.get("type"),
            "notification_event_code": latest.get("event_code"),
            "notification_history": deepcopy(
                messages[:_NOTIFICATION_ATTR_HISTORY_LIMIT]
            ),
            "notification_total": normalized.get("count"),
            "notification_count": normalized.get("count"),
            "notification_page": None,
            "notification_has_history_message": normalized.get(
                "has_history_message"
            ),
            "notification_next_message_id": normalized.get("next_message_id"),
            "notification_filter_state": _NOTIFICATION_FILTER,
            "notification_source": (
                "private_cloud_vehicle_message_feed" if cache is not None else None
            ),
            "notification_source_age": source_age,
            "notification_error": getattr(
                coordinator, "_beta26_notification_error", None
            ),
        }
    )
    return result


def _refresh_notification_cache(coordinator: Any) -> None:
    now = time.monotonic()
    last_attempt = getattr(
        coordinator, "_beta26_notification_last_attempt_mono", None
    )
    if (
        last_attempt is not None
        and now - float(last_attempt) < _NOTIFICATION_TTL_SECONDS
    ):
        return

    coordinator._beta26_notification_last_attempt_mono = now  # noqa: SLF001
    try:
        response = coordinator.client.notification_feed(  # type: ignore[attr-defined]
            coordinator.sn,
            message_id="",
            filter_state=_NOTIFICATION_FILTER,
        )
    except Exception as err:  # noqa: BLE001 - optional feed must not break core poll.
        coordinator._beta26_notification_error = (  # noqa: SLF001
            f"{type(err).__name__}: {err}"
        )
        return

    coordinator._beta26_notification_cache = response  # noqa: SLF001
    coordinator._beta26_notification_last_success_mono = now  # noqa: SLF001
    coordinator._beta26_notification_error = None  # noqa: SLF001


def _install_notification_sensor() -> None:
    from . import sensor as platform

    if any(description.key == "notification" for description in platform.SENSORS):
        return

    def value(data: dict[str, Any]) -> str | None:
        title = data.get("notification_title")
        if title:
            return str(title)[:255]
        if data.get("notification_count") == 0:
            return "No notifications"
        return None

    def attrs(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "message_id": data.get("notification_message_id"),
            "content": data.get("notification_content"),
            "created_at": data.get("notification_created_at"),
            "addtime": data.get("notification_addtime"),
            "read": data.get("notification_read"),
            "level": data.get("notification_level"),
            "type": data.get("notification_type"),
            "event_code": data.get("notification_event_code"),
            "count": data.get("notification_count"),
            "has_history_message": data.get("notification_has_history_message"),
            "next_message_id": data.get("notification_next_message_id"),
            "filter_state": data.get("notification_filter_state"),
            "source": data.get("notification_source"),
            "source_age": data.get("notification_source_age"),
            "last_error": data.get("notification_error"),
            "recent": deepcopy(
                (data.get("notification_history") or [])[
                    :_NOTIFICATION_ATTR_HISTORY_LIMIT
                ]
            ),
        }

    platform.SENSORS = (
        *platform.SENSORS,
        platform.NavimowSensorDescription(
            key="notification",
            name="Notification",
            icon="mdi:bell-outline",
            value_fn=value,
            attrs_fn=attrs,
        ),
    )


def install_beta26_runtime() -> None:
    """Install notification transport, polling and sensor once."""
    cls = _coordinator.NavimowCoordinator
    if getattr(cls, "_beta26_runtime_installed", False):
        _install_notification_sensor()
        return

    # Historical beta26 endpoint remains callable for explicit debugging only.
    if not hasattr(NavimowCloudClient, "notification_history"):
        def notification_history(
            self: NavimowCloudClient,
            sn: str,
            page: int = 1,
            size: int = _NOTIFICATION_PAGE_SIZE,
        ) -> dict[str, Any]:
            data = self.call(
                _LEGACY_NOTIFICATION_PATH,
                {
                    "vehicle_sn": str(sn),
                    "page": int(page),
                    "size": int(size),
                },
            )
            return data if isinstance(data, dict) else {}

        NavimowCloudClient.notification_history = notification_history  # type: ignore[attr-defined]

    if not hasattr(NavimowCloudClient, "notification_feed"):
        def notification_feed(
            self: NavimowCloudClient,
            sn: str,
            message_id: str = "",
            filter_state: str = _NOTIFICATION_FILTER,
        ) -> dict[str, Any]:
            data = self.call(
                _NOTIFICATION_PATH,
                {
                    "message_id": str(message_id or ""),
                    "vehicle_sn": str(sn),
                    "filter_state": str(filter_state or _NOTIFICATION_FILTER),
                },
            )
            return data if isinstance(data, dict) else {}

        NavimowCloudClient.notification_feed = notification_feed  # type: ignore[attr-defined]

    original_fetch = cls._fetch_blocking
    original_bootstrap = cls._bootstrap_snapshot

    def fetch_blocking(self: Any) -> dict[str, Any]:
        snapshot = original_fetch(self)
        _refresh_notification_cache(self)
        return _decorate_snapshot(self, snapshot)

    def bootstrap_snapshot(self: Any) -> dict[str, Any]:
        return _decorate_snapshot(self, original_bootstrap(self))

    cls._fetch_blocking = fetch_blocking
    cls._bootstrap_snapshot = bootstrap_snapshot
    cls._beta26_runtime_installed = True
    _install_notification_sensor()
