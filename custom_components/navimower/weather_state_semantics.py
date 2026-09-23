"""Vendor weather decision polling and composed mower status semantics."""
from __future__ import annotations

from copy import deepcopy
import time
from typing import Any

from .coordinator import NavimowCoordinator

_INSTALLED = False

VENDOR_WEATHER_PATH = "/vehicle/vehicle/get-vehicle-weather"
WEATHER_POLL_ACTIVE_SECONDS = 15
WEATHER_POLL_IDLE_SECONDS = 30
WEATHER_FRESH_SECONDS = 120.0

# These are current vendor decisions, not configuration switches. The recovered
# vendor contract and live probes use 1 for an active condition.
_WEATHER_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("rain", "rainState", "rain_delay", "Rain delay"),
    ("snow", "snowState", "snow_delay", "Snow delay"),
    ("wind", "stormState", "wind_delay", "Wind delay"),
    ("frost", "frostState", "frost_delay", "Frost delay"),
    (
        "high_temperature",
        "highTemperatureState",
        "high_temperature_delay",
        "High temperature delay",
    ),
)


def _as_active(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    try:
        return int(float(value)) != 0
    except (TypeError, ValueError):
        text = str(value or "").strip().lower()
        return text in {"true", "on", "yes", "active"}


def _endpoint_age(coordinator: NavimowCoordinator) -> float | None:
    status = coordinator._endpoint_status.get("vehicle_weather")  # noqa: SLF001
    if not isinstance(status, dict):
        return None
    last = status.get("last_success_mono")
    if last is None:
        return None
    try:
        return max(0.0, time.monotonic() - float(last))
    except (TypeError, ValueError):
        return None


def normalize_vendor_weather(
    raw: Any,
    *,
    age_s: float | None,
) -> dict[str, Any]:
    """Normalize the private weather endpoint into one stable decision model."""
    row = raw if isinstance(raw, dict) else {}
    available = any(field in row for _, field, _, _ in _WEATHER_FIELDS)
    active: list[dict[str, str]] = []
    for reason, field, state, label in _WEATHER_FIELDS:
        if field in row and _as_active(row.get(field)):
            active.append(
                {
                    "reason": reason,
                    "field": field,
                    "state": state,
                    "label": label,
                }
            )

    if not available:
        state = "unavailable"
        label = None
        reason = None
        hold_active: bool | None = None
    elif not active:
        state = "clear"
        label = None
        reason = None
        hold_active = False
    elif len(active) == 1:
        state = active[0]["state"]
        label = active[0]["label"]
        reason = active[0]["reason"]
        hold_active = True
    else:
        state = "weather_delay"
        label = "Weather delay"
        reason = active[0]["reason"]
        hold_active = True

    return {
        "available": available,
        "fresh": bool(
            available
            and age_s is not None
            and age_s <= WEATHER_FRESH_SECONDS
        ),
        "age_s": round(age_s, 1) if age_s is not None else None,
        "state": state,
        "hold_active": hold_active,
        "hold_reason": reason,
        "hold_reasons": [item["reason"] for item in active],
        "label": label,
        "rain_level": row.get("rainLevel"),
        "raw": {
            field: row.get(field)
            for _, field, _, _ in _WEATHER_FIELDS
            if field in row
        }
        | ({"rainLevel": row.get("rainLevel")} if "rainLevel" in row else {}),
        "source": "private_cloud_vehicle_weather" if available else None,
    }


def _decorate_snapshot(
    coordinator: NavimowCoordinator,
    snapshot: dict[str, Any],
) -> None:
    raw = coordinator._raw_cache.get("vehicle_weather")  # noqa: SLF001
    weather = normalize_vendor_weather(raw, age_s=_endpoint_age(coordinator))
    snapshot["vendor_weather"] = weather
    snapshot["weather_state"] = weather["state"]
    snapshot["weather_state_source"] = weather["source"]
    snapshot["weather_state_age"] = weather["age_s"]
    snapshot["weather_state_fresh"] = weather["fresh"]
    snapshot["weather_hold_active"] = weather["hold_active"]
    snapshot["weather_hold_reason"] = weather["hold_reason"]
    snapshot["weather_hold_reasons"] = weather["hold_reasons"]
    snapshot["weather_rain_level"] = weather["rain_level"]

    # Keep HA's physical lawn_mower activity canonical. This display state is
    # for the user-facing Status sensor / mower attributes only.
    base_state = snapshot.get("state")
    if (
        weather["hold_active"] is True
        and weather["label"]
        and snapshot.get("error") is not True
    ):
        snapshot["display_state"] = weather["label"]
    else:
        snapshot["display_state"] = base_state

    diagnostic_raw = snapshot.get("raw")
    if isinstance(diagnostic_raw, dict) and isinstance(raw, dict):
        diagnostic_raw["vehicle_weather"] = deepcopy(raw)


def _refresh_vendor_weather(
    coordinator: NavimowCoordinator,
    snapshot: dict[str, Any],
) -> None:
    now = time.monotonic()
    ttl = (
        WEATHER_POLL_ACTIVE_SECONDS
        if coordinator._private_poll_active()  # noqa: SLF001
        else WEATHER_POLL_IDLE_SECONDS
    )
    coordinator._fetch_endpoint(  # noqa: SLF001
        coordinator._raw_cache,  # noqa: SLF001
        "vehicle_weather",
        lambda: coordinator.client.call(
            VENDOR_WEATHER_PATH,
            {"vehicle_sn": coordinator.sn},
        ),
        ttl=ttl,
        now=now,
    )
    _decorate_snapshot(coordinator, snapshot)


def install_weather_state_semantics() -> None:
    """Install bounded vendor weather polling before scheduler evaluation."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_fetch_blocking = NavimowCoordinator._fetch_blocking

    def fetch_blocking(self: NavimowCoordinator) -> dict[str, Any]:
        snapshot = original_fetch_blocking(self)
        _refresh_vendor_weather(self, snapshot)
        return snapshot

    NavimowCoordinator._fetch_blocking = fetch_blocking  # type: ignore[method-assign]
    _INSTALLED = True
