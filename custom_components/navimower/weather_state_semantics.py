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

# These fields are vendor *current decision* state, not configuration switches.
# Live H2 evidence has confirmed 0 = inactive and 1 = active. Preserve any
# unexpected value as unknown instead of silently assigning it semantics.
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


def _flag_state(value: Any) -> bool | None:
    """Return True/False only for the observed vendor 1/0 flag contract."""
    if isinstance(value, bool):
        return value
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        return None
    if numeric == 0:
        return False
    if numeric == 1:
        return True
    return None


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
    inactive: list[str] = []
    unknown: list[str] = []
    normalized: dict[str, bool | None] = {}

    for reason, field, state, label in _WEATHER_FIELDS:
        if field not in row:
            continue
        parsed = _flag_state(row.get(field))
        normalized[reason] = parsed
        if parsed is True:
            active.append(
                {
                    "reason": reason,
                    "field": field,
                    "state": state,
                    "label": label,
                }
            )
        elif parsed is False:
            inactive.append(reason)
        else:
            unknown.append(reason)

    if not available:
        state = "unavailable"
        label = None
        reason = None
        hold_active: bool | None = None
    elif active:
        hold_active = True
        if len(active) == 1:
            state = active[0]["state"]
            label = active[0]["label"]
            reason = active[0]["reason"]
        else:
            state = "weather_delay"
            label = "Weather delay"
            reason = active[0]["reason"]
    elif unknown:
        state = "unknown"
        label = None
        reason = None
        hold_active = None
    else:
        state = "clear"
        label = None
        reason = None
        hold_active = False

    fresh = bool(
        available
        and age_s is not None
        and age_s <= WEATHER_FRESH_SECONDS
    )
    raw_states = {
        field: row.get(field)
        for _, field, _, _ in _WEATHER_FIELDS
        if field in row
    }
    if "rainLevel" in row:
        raw_states["rainLevel"] = row.get("rainLevel")

    return {
        "available": available,
        "fresh": fresh,
        "age_s": round(age_s, 1) if age_s is not None else None,
        "state": state,
        "hold_active": hold_active,
        "hold_reason": reason,
        "hold_reasons": [item["reason"] for item in active],
        "inactive_reasons": inactive,
        "unknown_reasons": unknown,
        "label": label,
        "rain_level": row.get("rainLevel"),
        "rain_state": normalized.get("rain"),
        "snow_state": normalized.get("snow"),
        "storm_state": normalized.get("wind"),
        "frost_state": normalized.get("frost"),
        "high_temperature_state": normalized.get("high_temperature"),
        "raw": raw_states,
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
    snapshot["weather_unknown_reasons"] = weather["unknown_reasons"]
    snapshot["weather_rain_state"] = weather["rain_state"]
    snapshot["weather_snow_state"] = weather["snow_state"]
    snapshot["weather_storm_state"] = weather["storm_state"]
    snapshot["weather_frost_state"] = weather["frost_state"]
    snapshot["weather_high_temperature_state"] = weather["high_temperature_state"]
    snapshot["weather_rain_level"] = weather["rain_level"]

    # Home Assistant's lawn_mower activity remains physical/canonical. A fresh
    # vendor weather decision composes only the human-facing status layer.
    base_state = snapshot.get("state")
    if (
        weather["fresh"]
        and weather["hold_active"] is True
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
