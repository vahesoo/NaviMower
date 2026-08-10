"""Beta26 live vendor notification history support.

The exact message-history route and payload were recovered from Navimow's public
H5 bundle in beta25.  This runtime keeps the integration-side implementation
small and conservative:

* use the existing authenticated p:101 private-cloud transport,
* poll the read-only history endpoint at most once per minute,
* retain the last successful response across transient failures,
* expose only the newest message plus a bounded recent list to Home Assistant,
* never mark messages read and never call the message-detail endpoint.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import time
from typing import Any

from . import coordinator as _coordinator
from .api.client import NavimowCloudClient

_NOTIFICATION_PATH = "/mowerbot/user/message/get-vehicle-history-message"
_NOTIFICATION_TTL_SECONDS = 60
_NOTIFICATION_PAGE_SIZE = 20
_NOTIFICATION_ATTR_HISTORY_LIMIT = 5


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
    if not isinstance(item, dict):
        return None
    addtime = item.get("addtime")
    return {
        "id": item.get("id"),
        "message_id": item.get("message_id"),
        "title": _bounded_text(item.get("title"), 255),
        "content": _bounded_text(item.get("content"), 1200),
        "addtime": addtime,
        "created_at": _created_at(addtime),
        "read": _as_int(item.get("read")),
        "level": _as_int(item.get("level")),
    }


def _normalize_response(value: Any) -> dict[str, Any]:
    response = value if isinstance(value, dict) else {}
    messages: list[dict[str, Any]] = []
    for item in response.get("list") or []:
        normalized = _normalize_item(item)
        if normalized is not None:
            messages.append(normalized)
    return {
        "list": messages,
        "total": _as_int(response.get("total")),
        "page": _as_int(response.get("page")),
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
            "notification_history": deepcopy(
                messages[:_NOTIFICATION_ATTR_HISTORY_LIMIT]
            ),
            "notification_total": normalized.get("total"),
            "notification_page": normalized.get("page"),
            "notification_source": (
                "private_cloud_message_history" if cache is not None else None
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
        response = coordinator.client.notification_history(  # type: ignore[attr-defined]
            coordinator.sn,
            page=1,
            size=_NOTIFICATION_PAGE_SIZE,
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
        if data.get("notification_total") == 0:
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
            "total": data.get("notification_total"),
            "page": data.get("notification_page"),
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
    """Install notification-history transport, polling and sensor once."""
    cls = _coordinator.NavimowCoordinator
    if getattr(cls, "_beta26_runtime_installed", False):
        _install_notification_sensor()
        return

    if not hasattr(NavimowCloudClient, "notification_history"):
        def notification_history(
            self: NavimowCloudClient,
            sn: str,
            page: int = 1,
            size: int = _NOTIFICATION_PAGE_SIZE,
        ) -> dict[str, Any]:
            data = self.call(
                _NOTIFICATION_PATH,
                {
                    "vehicle_sn": str(sn),
                    "page": int(page),
                    "size": int(size),
                },
            )
            return data if isinstance(data, dict) else {}

        NavimowCloudClient.notification_history = notification_history  # type: ignore[attr-defined]

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
