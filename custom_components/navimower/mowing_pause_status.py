"""Expose a conservative mowing-pause reason for UI and automations.

The mower's generic Returning/Docked states do not identify *why* mowing was
interrupted.  Prefer a fresh current vendor weather hold when one is available,
then reuse the notification center's existing transition attribution.  Do not
call a low-battery pause automation-safe until the vendor Device notification
feed confirms the low-battery return (currently code 1502).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

LOW_BATTERY_VENDOR_CODES = frozenset({"1502"})
LOW_BATTERY_CONFIRM_WINDOW_SECONDS = 300.0


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> float | None:
    if value is None:
        return None
    numeric = _as_float(value)
    if numeric is not None:
        if numeric > 10_000_000_000:
            numeric /= 1000.0
        return numeric if numeric > 0 else None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _vendor_message_timestamp(item: dict[str, Any]) -> float | None:
    stamp = _timestamp(item.get("addtime"))
    if stamp is not None:
        return stamp
    return _timestamp(item.get("created_at"))


def _vendor_code(item: dict[str, Any]) -> str | None:
    for key in (
        "notification_code",
        "vendor_code",
        "error_code",
        "event_code",
    ):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip().upper()
    return None


def _low_battery_confirmation(
    vendor_messages: list[dict[str, Any]] | None,
    paused_at: Any,
) -> dict[str, Any] | None:
    """Return a time-matched vendor low-battery row, never an unrelated old row."""
    paused_stamp = _timestamp(paused_at)
    if paused_stamp is None:
        return None
    for item in vendor_messages or []:
        if not isinstance(item, dict) or _vendor_code(item) not in LOW_BATTERY_VENDOR_CODES:
            continue
        message_stamp = _vendor_message_timestamp(item)
        if message_stamp is None:
            continue
        if abs(message_stamp - paused_stamp) <= LOW_BATTERY_CONFIRM_WINDOW_SECONDS:
            return item
    return None


def classify_mowing_pause(
    *,
    interrupted_reason: Any,
    active_task: dict[str, Any] | None,
    settings: dict[str, Any] | None,
    vendor_messages: list[dict[str, Any]] | None,
    weather_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a stable status model for the current retained mowing interruption.

    ``low_battery_pending`` is deliberately not automation-safe.  The underlying
    transition is inferred from Mowing -> Returning/Docked plus the configured
    return-battery threshold after excluding known HA Dock, night and schedule
    stop cases.  ``low_battery`` additionally requires a matching vendor 1502
    notification around the same interruption time.
    """
    task = active_task if isinstance(active_task, dict) else {}
    config = settings if isinstance(settings, dict) else {}
    weather = weather_status if isinstance(weather_status, dict) else {}
    transition_reason = str(interrupted_reason or "").strip().lower() or None

    weather_fresh = weather.get("fresh") is True
    weather_active = weather_fresh and weather.get("hold_active") is True
    weather_reason = str(weather.get("hold_reason") or "").strip().lower() or None
    weather_reasons = [
        str(value).strip().lower()
        for value in (weather.get("hold_reasons") or [])
        if str(value).strip()
    ]
    weather_state = str(weather.get("state") or "").strip().lower() or None

    # The current vendor weather decision is stronger than an older transition
    # attribution such as manual_dock.  This is intentionally freshness-gated:
    # a stale weather row must never hide a newer manual/charging/night reason.
    if weather_active:
        if len(weather_reasons) > 1 or weather_state == "weather_delay":
            state = "weather"
        else:
            state = weather_reason or "weather"
        reason = weather_reason or "weather"
        paused_at = None
        confirmation = None
        confidence = "vendor_weather_state"
    else:
        reason = transition_reason
        paused_at = task.get("charging_paused_at") if reason == "charging" else task.get("night_paused_at")
        confirmation = (
            _low_battery_confirmation(vendor_messages, paused_at)
            if reason == "charging"
            else None
        )

        if reason is None:
            state = "none"
            confidence = None
        elif reason == "charging" and confirmation is not None:
            state = "low_battery"
            confidence = "vendor_reported"
        elif reason == "charging":
            state = "low_battery_pending"
            confidence = task.get("charging_pause_confidence") or "inferred_from_return_battery_threshold"
        elif reason == "night":
            state = "night"
            confidence = "inferred_from_sunset_and_night_mowing_off"
        elif reason == "manual_dock":
            state = "manual_dock"
            confidence = "confirmed_ha_command"
        elif reason == "unknown":
            state = "unknown"
            confidence = "unattributed"
        else:
            state = reason
            confidence = "attributed"

    confirmation_code = _vendor_code(confirmation or {})
    confirmation_stamp = _vendor_message_timestamp(confirmation or {})
    confirmation_at = (
        datetime.fromtimestamp(confirmation_stamp, UTC).isoformat()
        if confirmation_stamp is not None
        else None
    )

    return {
        "state": state,
        "reason": reason,
        "confidence": confidence,
        "underlying_interrupted_reason": transition_reason,
        "weather_state": weather_state,
        "weather_hold_active": weather_active,
        "weather_hold_reason": weather_reason,
        "weather_hold_reasons": weather_reasons,
        "weather_source": weather.get("source"),
        "weather_age_s": weather.get("age_s"),
        "weather_fresh": weather_fresh,
        "automation_safe_weather": weather_active,
        "low_battery_confirmed": state == "low_battery",
        "automation_safe_low_battery": state == "low_battery",
        "progress_before_pause": task.get("progress_before_pause"),
        "battery_before_pause": task.get("battery_before_pause"),
        "return_battery_level": _as_float(config.get("return_battery_level")),
        "paused_at": paused_at,
        "task_id": task.get("task_id"),
        "task_origin": task.get("origin"),
        "task_trigger": task.get("trigger"),
        "vendor_confirmation_code": confirmation_code,
        "vendor_confirmation_at": confirmation_at,
    }


def _status_for_snapshot(coordinator: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    center = getattr(coordinator, "notification_center", None)
    task = center.active_task if center is not None else None
    reason = center.interrupted_reason if center is not None else None
    vendor_cache = getattr(coordinator, "_notification_cache", None)
    vendor_messages = (
        vendor_cache.get("list")
        if isinstance(vendor_cache, dict) and isinstance(vendor_cache.get("list"), list)
        else []
    )
    return classify_mowing_pause(
        interrupted_reason=reason,
        active_task=task,
        settings=snapshot.get("settings") if isinstance(snapshot, dict) else None,
        vendor_messages=vendor_messages,
        weather_status={
            "state": snapshot.get("weather_state"),
            "hold_active": snapshot.get("weather_hold_active"),
            "hold_reason": snapshot.get("weather_hold_reason"),
            "hold_reasons": snapshot.get("weather_hold_reasons"),
            "source": snapshot.get("weather_state_source"),
            "age_s": snapshot.get("weather_state_age"),
            "fresh": snapshot.get("weather_state_fresh"),
        },
    )


def _install_sensor() -> None:
    from . import sensor as platform

    if any(description.key == "mowing_pause_reason" for description in platform.SENSORS):
        return

    platform.SENSORS = (
        *platform.SENSORS,
        platform.NavimowSensorDescription(
            key="mowing_pause_reason",
            name="Mowing pause reason",
            icon="mdi:pause-circle-outline",
            value_fn=lambda data: data.get("mowing_pause_reason"),
            attrs_fn=lambda data: dict(data.get("mowing_pause_status") or {}),
        ),
    )


def install_mowing_pause_status() -> None:
    """Install snapshot decoration and the public status sensor once."""
    from . import notification_feed as feed

    if not getattr(feed, "_mowing_pause_status_installed", False):
        original_decorate = feed._decorate_snapshot

        def decorate_snapshot(coordinator: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
            result = original_decorate(coordinator, snapshot)
            status = _status_for_snapshot(coordinator, result)
            result["mowing_pause_reason"] = status.get("state")
            result["mowing_pause_status"] = {
                key: value for key, value in status.items() if key != "state"
            }
            return result

        feed._decorate_snapshot = decorate_snapshot
        feed._mowing_pause_status_installed = True

    _install_sensor()
