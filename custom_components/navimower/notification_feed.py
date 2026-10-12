"""Navimow vendor notifications plus the Navimower local notification center.

The vendor side uses the main app Notification -> Device feed contract recovered
through the 0.4.1 beta line:

* POST ``/mowerbot/user/message/vehicleMessageListField`` through p:101,
* payload ``message_id`` + ``vehicle_sn`` + ``filter_state``,
* first page uses an empty ``message_id`` and ``filter_state=all``,
* response exposes ``vehicle_message_list`` and ``has_history_message``.

Vendor polling remains read-only and rate-limited. The integration retains at most
10 normalized vendor rows and merges them newest-first with up to 20 persistent
Navimower-generated rows. Local rows have their own read state and never get
sent to a Navimow message-detail endpoint.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import re
import time
from typing import Any

from . import coordinator as _coordinator
from .api.client import NavimowCloudClient
from .notification_center import (
    MERGED_NOTIFICATION_LIMIT,
    VENDOR_NOTIFICATION_LIMIT,
    merge_notification_lists,
)

_NOTIFICATION_PATH = "/mowerbot/user/message/vehicleMessageListField"
_NOTIFICATION_FILTER = "all"
_NOTIFICATION_TTL_SECONDS = 60
# Entity attributes are intentionally smaller than the internal merged history so Recorder stays below its 16 KiB state-attribute limit.
_NOTIFICATION_ATTR_HISTORY_LIMIT = 5
_VENDOR_REPEAT_WINDOW_SECONDS = 5 * 60
_UNRESOLVED_VENDOR_TITLE_RE = re.compile(r"%(?:\d+\$)?[@A-Za-z]|\{\{[^{}]+\}\}")
_VENDOR_TITLE_OVERRIDES = {
    # Confirmed H-series vendor code. The Device feed can return the broken
    # localized title "Completed on %@" while the body is the real 1003 fault.
    "1003": "Mowing motor undervoltage protection",
}

# Legacy notification-history route kept as a callable compatibility/debug helper only.
_LEGACY_NOTIFICATION_PATH = "/mowerbot/user/message/get-vehicle-history-message"
_NOTIFICATION_PAGE_SIZE = 20


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    """Preserve vendor read state as a boolean when it is representable."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value == 0:
            return False
        if value == 1:
            return True
        return None
    text = str(value).strip().lower()
    if text in {"0", "false", "no", "off"}:
        return False
    if text in {"1", "true", "yes", "on"}:
        return True
    return None


def _bounded_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _code_text(value: Any) -> str | None:
    """Keep vendor notification codes verbatim, including alphanumeric codes."""
    return _bounded_text(value, 64)


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


def _message_epoch(item: dict[str, Any]) -> float | None:
    """Return one notification timestamp in epoch seconds when available."""
    raw = item.get("addtime")
    try:
        stamp = float(raw)
    except (TypeError, ValueError):
        stamp = 0.0
    if stamp > 10_000_000_000:
        stamp /= 1000.0
    if stamp > 0:
        return stamp
    created = item.get("created_at")
    try:
        parsed = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _message_age(item: dict[str, Any]) -> float | None:
    stamp = _message_epoch(item)
    if stamp is None:
        return None
    return max(0.0, datetime.now(UTC).timestamp() - stamp)


def _canonical_vendor_title(title: Any, vendor_code: str | None) -> str | None:
    """Hide unresolved vendor localization placeholders from the public feed."""
    text = _bounded_text(title, 255)
    if text and not _UNRESOLVED_VENDOR_TITLE_RE.search(text):
        return text
    if vendor_code and vendor_code in _VENDOR_TITLE_OVERRIDES:
        return _VENDOR_TITLE_OVERRIDES[vendor_code]
    if vendor_code:
        return f"Mower notification ({vendor_code})"
    return "Mower notification" if text else None


def _notification_fingerprint(item: dict[str, Any]) -> tuple[str, str] | None:
    """Return the stable identity used only for short-window vendor dedupe."""
    code = str(item.get("vendor_code") or "").strip()
    content = " ".join(str(item.get("content") or "").split()).casefold()
    if not code or not content:
        return None
    return code, content


def _group_vendor_messages(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Collapse repeated copies of one vendor event into a single visible row.

    The grouping window is anchored to the newest occurrence, so a fault that
    repeats again later becomes a new event instead of growing one group forever.
    """
    ordered = sorted(
        (deepcopy(item) for item in messages),
        key=lambda item: _message_epoch(item) or 0.0,
        reverse=True,
    )
    buckets: list[dict[str, Any]] = []
    for item in ordered:
        fingerprint = _notification_fingerprint(item)
        stamp = _message_epoch(item)
        target: dict[str, Any] | None = None
        if fingerprint is not None and stamp is not None:
            for bucket in buckets:
                if bucket["fingerprint"] != fingerprint:
                    continue
                anchor = bucket["anchor"]
                if anchor is not None and 0 <= anchor - stamp <= _VENDOR_REPEAT_WINDOW_SECONDS:
                    target = bucket
                    break
        if target is None:
            buckets.append(
                {
                    "item": item,
                    "fingerprint": fingerprint,
                    "anchor": stamp,
                    "ids": [
                        str(item.get("message_id"))
                    ] if item.get("message_id") not in (None, "") else [],
                    "reads": [item.get("read")],
                }
            )
            continue
        if item.get("message_id") not in (None, ""):
            message_id = str(item.get("message_id"))
            if message_id not in target["ids"]:
                target["ids"].append(message_id)
        target["reads"].append(item.get("read"))

    visible: list[dict[str, Any]] = []
    member_map: dict[str, list[str]] = {}
    for bucket in buckets:
        row = bucket["item"]
        ids = list(bucket["ids"])
        reads = list(bucket["reads"])
        row["repeat_count"] = max(1, len(ids) or len(reads))
        if any(value is False for value in reads):
            row["read"] = False
        elif reads and all(value is True for value in reads):
            row["read"] = True
        elif any(value is True for value in reads):
            row["read"] = None
        representative = row.get("message_id")
        if representative not in (None, "") and ids:
            member_map[str(representative)] = ids
        visible.append(row)
    return visible, member_map


def _normalize_item(item: Any) -> dict[str, Any] | None:
    """Normalize confirmed vendor notification fields without inventing values."""
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
    vendor_code = _code_text(
        _first_present(
            item,
            "error_code",
            "errorCode",
            "event_code",
            "eventCode",
            "message_code",
            "messageCode",
        )
    )
    raw_title = _first_present(item, "title", "message_title", "messageTitle")
    return {
        "id": _first_present(item, "id", "message_record_id"),
        "message_id": _first_present(item, "message_id", "messageId"),
        "title": _canonical_vendor_title(raw_title, vendor_code),
        "content": _bounded_text(
            _first_present(item, "content", "message_content", "messageContent"),
            1200,
        ),
        "addtime": addtime,
        "created_at": _created_at(addtime),
        "read": _as_bool(_first_present(item, "read", "is_read", "isRead")),
        "level": _as_int(_first_present(item, "level", "message_level")),
        "type": _first_present(item, "type", "message_type", "messageType"),
        "style": _first_present(item, "style", "message_style", "messageStyle"),
        "variable": deepcopy(item.get("variable")),
        # Canonical notification-code naming. These are notification/event codes, not
        # necessarily faults: real feeds include values such as 150A.
        "notification_code": vendor_code,
        "vendor_code": vendor_code,
        "error_code": vendor_code,
        # Backward-compatible alias retained for existing automations.
        "event_code": vendor_code,
        "origin": "vendor",
        "kind": None,
        "confidence": "vendor_reported",
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
        "next_message_id": messages[-1].get("message_id") if messages else None,
    }


def _decorate_snapshot(coordinator: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    result = dict(snapshot)
    cache = getattr(coordinator, "_notification_cache", None)
    normalized = _normalize_response(cache)
    raw_vendor_messages = normalized["list"]
    grouped_vendor_messages, group_members = _group_vendor_messages(raw_vendor_messages)
    vendor_messages = grouped_vendor_messages[:VENDOR_NOTIFICATION_LIMIT]
    visible_ids = {
        str(item.get("message_id"))
        for item in vendor_messages
        if item.get("message_id") not in (None, "")
    }
    coordinator._notification_group_members = {  # noqa: SLF001
        key: value for key, value in group_members.items() if key in visible_ids
    }
    center = getattr(coordinator, "notification_center", None)
    local_messages = center.messages if center is not None else []
    messages = merge_notification_lists(vendor_messages, local_messages)
    latest = messages[0] if messages else {}

    last_success = getattr(coordinator, "_notification_last_success_mono", None)
    vendor_source_age = (
        max(0.0, time.monotonic() - float(last_success))
        if last_success is not None
        else None
    )
    latest_origin = latest.get("origin")
    latest_source_age = (
        vendor_source_age
        if latest_origin == "vendor"
        else (_message_age(latest) if latest_origin == "navimower" else None)
    )
    if vendor_messages and local_messages:
        source = "merged_vendor_and_navimower"
    elif local_messages:
        source = "navimower_local"
    elif vendor_messages or cache is not None:
        source = "private_cloud_vehicle_message_feed"
    else:
        source = None

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
            "notification_style": latest.get("style"),
            "notification_variable": deepcopy(latest.get("variable")),
            "notification_code": latest.get("notification_code"),
            "notification_vendor_code": latest.get("vendor_code"),
            "notification_error_code": latest.get("error_code"),
            "notification_event_code": latest.get("event_code"),
            "notification_origin": latest_origin,
            "notification_kind": latest.get("kind"),
            "notification_confidence": latest.get("confidence"),
            "notification_history": deepcopy(messages[:_NOTIFICATION_ATTR_HISTORY_LIMIT]),
            "notification_total": len(messages),
            "notification_count": len(messages),
            "notification_vendor_count": len(vendor_messages),
            "notification_vendor_raw_count": len(raw_vendor_messages),
            "notification_local_count": len(local_messages),
            "notification_page": None,
            "notification_has_history_message": normalized.get("has_history_message"),
            "notification_next_message_id": normalized.get("next_message_id"),
            "notification_filter_state": _NOTIFICATION_FILTER,
            "notification_source": source,
            "notification_source_age": latest_source_age,
            "notification_vendor_source_age": vendor_source_age,
            "notification_error": getattr(coordinator, "_notification_error", None),
        }
    )
    return result


def refresh_notification_snapshot(coordinator: Any) -> None:
    """Publish current vendor/local notification state without a remote call."""
    current = coordinator.data
    if not isinstance(current, dict):
        return
    coordinator.async_set_updated_data(_decorate_snapshot(coordinator, current))


def _refresh_notification_cache(coordinator: Any) -> None:
    now = time.monotonic()
    last_attempt = getattr(coordinator, "_notification_last_attempt_mono", None)
    if (
        last_attempt is not None
        and now - float(last_attempt) < _NOTIFICATION_TTL_SECONDS
    ):
        return

    coordinator._notification_last_attempt_mono = now  # noqa: SLF001
    try:
        response = coordinator.client.notification_feed(  # type: ignore[attr-defined]
            coordinator.sn,
            message_id="",
            filter_state=_NOTIFICATION_FILTER,
        )
    except Exception as err:  # noqa: BLE001 - optional feed must not break core poll.
        coordinator._notification_error = f"{type(err).__name__}: {err}"  # noqa: SLF001
        return

    # Keep only the newest ten normalized read-only vendor rows. Navimower-local
    # rows have a separate bounded persistent Store and are merged during snapshot
    # decoration instead of being inserted into the vendor cache.
    coordinator._notification_raw_cache = deepcopy(response)  # noqa: SLF001
    normalized = _normalize_response(response)
    vendor_messages = normalized["list"][:VENDOR_NOTIFICATION_LIMIT]
    coordinator._notification_cache = {  # noqa: SLF001
        "list": deepcopy(vendor_messages),
        "has_history_message": normalized.get("has_history_message"),
    }
    coordinator._notification_last_success_mono = now  # noqa: SLF001
    coordinator._notification_error = None  # noqa: SLF001


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
            "style": data.get("notification_style"),
            "variable": deepcopy(data.get("notification_variable")),
            "notification_code": data.get("notification_code"),
            "vendor_code": data.get("notification_vendor_code"),
            "error_code": data.get("notification_error_code"),
            "event_code": data.get("notification_event_code"),
            "origin": data.get("notification_origin"),
            "kind": data.get("notification_kind"),
            "confidence": data.get("notification_confidence"),
            "count": data.get("notification_count"),
            "vendor_count": data.get("notification_vendor_count"),
            "vendor_raw_count": data.get("notification_vendor_raw_count"),
            "local_count": data.get("notification_local_count"),
            "has_history_message": data.get("notification_has_history_message"),
            "next_message_id": data.get("notification_next_message_id"),
            "filter_state": data.get("notification_filter_state"),
            "source": data.get("notification_source"),
            "source_age": data.get("notification_source_age"),
            "vendor_source_age": data.get("notification_vendor_source_age"),
            "last_error": data.get("notification_error"),
            "recent": deepcopy(
                (data.get("notification_history") or [])[:_NOTIFICATION_ATTR_HISTORY_LIMIT]
            ),
        }

    platform.SENSORS = (
        *platform.SENSORS,
        platform.NavimowSensorDescription(
            key="notification",
            name="Latest notification",
            icon="mdi:bell-outline",
            value_fn=value,
            attrs_fn=attrs,
        ),
    )


def install_notification_feed() -> None:
    """Install notification transport, polling and sensor once."""
    cls = _coordinator.NavimowCoordinator
    if getattr(cls, "_notification_feed_installed", False):
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
                    "vehicle_sn": sn,
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
    cls._notification_feed_installed = True
    _install_notification_sensor()
