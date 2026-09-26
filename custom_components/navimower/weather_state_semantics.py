"""Vendor weather decision polling and composed mower status semantics."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import logging
import math
import time
from typing import Any

from .coordinator import NavimowCoordinator

_LOGGER = logging.getLogger(__name__)
_INSTALLED = False

VENDOR_WEATHER_PATH = "/vehicle/vehicle/get-vehicle-weather"
WEATHER_POLL_ACTIVE_SECONDS = 15
WEATHER_POLL_IDLE_SECONDS = 30
WEATHER_FRESH_SECONDS = 120.0
_WEATHER_RUNTIME_STORE_KEY = "weather_runtime"

# These fields are vendor *current decision* state, not configuration switches.
# Live H2 evidence has confirmed 0 = inactive and 1 = active. Preserve any
# unexpected value as unknown instead of silently assigning it semantics.
#
# rainState is the current rain decision. It is deliberately exposed as
# "raining" while active; the post-rain "rain_delay" state is derived separately
# from the observed 1 -> 0 transition plus delayedPileSwitch/delayedPileSet.
_WEATHER_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("rain", "rainState", "raining", "Raining"),
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


def _rain_runtime(coordinator: NavimowCoordinator) -> dict[str, Any]:
    runtime = getattr(coordinator, "_rain_delay_runtime", None)
    if not isinstance(runtime, dict):
        runtime = {
            "last_rain_state": None,
            "last_rain_state_at": None,
            "delay_started_at": None,
            "delay_until": None,
            "delay_minutes": 0,
        }
        coordinator._rain_delay_runtime = runtime  # type: ignore[attr-defined]  # noqa: SLF001
    return runtime


def _mark_rain_runtime_dirty(coordinator: NavimowCoordinator) -> None:
    coordinator._rain_delay_persist_dirty = True  # type: ignore[attr-defined]  # noqa: SLF001


def _clear_rain_delay(coordinator: NavimowCoordinator) -> bool:
    runtime = _rain_runtime(coordinator)
    changed = any(
        runtime.get(key) not in (None, 0)
        for key in ("delay_started_at", "delay_until", "delay_minutes")
    )
    runtime["delay_started_at"] = None
    runtime["delay_until"] = None
    runtime["delay_minutes"] = 0
    if changed:
        _mark_rain_runtime_dirty(coordinator)
    return changed


def _start_rain_delay(
    coordinator: NavimowCoordinator,
    *,
    minutes: int,
    now: float,
) -> None:
    runtime = _rain_runtime(coordinator)
    runtime["delay_started_at"] = now
    runtime["delay_until"] = now + max(0, int(minutes)) * 60
    runtime["delay_minutes"] = max(0, int(minutes))
    _mark_rain_runtime_dirty(coordinator)


def _raw_rain_delay_quarters(snapshot: dict[str, Any]) -> int | None:
    """Decode delayedPileSet as the vendor quarter-hour wire value.

    The app/select contract uses hexadecimal strings (02 = 2 quarters,
    0C = 12 quarters, 10 = 16 quarters). A numeric cloud value is already a
    quarter-hour count and is therefore consumed directly.
    """
    raw = snapshot.get("raw") if isinstance(snapshot, dict) else None
    set_list = raw.get("set_list") if isinstance(raw, dict) else None
    value = set_list.get("delayedPileSet") if isinstance(set_list, dict) else None
    if value is not None:
        try:
            if isinstance(value, str):
                text = value.strip()
                return int(text, 16) if text else None
            return int(float(value))
        except (TypeError, ValueError):
            pass

    settings = snapshot.get("settings") if isinstance(snapshot, dict) else None
    fallback = settings.get("rain_delay_wire") if isinstance(settings, dict) else None
    try:
        return int(fallback) if fallback is not None else None
    except (TypeError, ValueError):
        return None


def _rain_delay_config(snapshot: dict[str, Any]) -> tuple[bool, int]:
    settings = snapshot.get("settings") if isinstance(snapshot, dict) else None
    enabled = bool(
        isinstance(settings, dict) and settings.get("rain_behavior") is True
    )
    quarters = _raw_rain_delay_quarters(snapshot)
    minutes = max(0, int(quarters or 0) * 15)
    return enabled, minutes


def _iso_timestamp(value: Any) -> str | None:
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    return datetime.fromtimestamp(stamp, UTC).isoformat()


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
        "vendor_fresh": fresh,
        "age_s": round(age_s, 1) if age_s is not None else None,
        "state": state,
        "vendor_state": state,
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
        "vendor_source": "private_cloud_vehicle_weather" if available else None,
    }


def _compose_rain_delay(
    coordinator: NavimowCoordinator,
    snapshot: dict[str, Any],
    weather: dict[str, Any],
) -> dict[str, Any]:
    """Compose deterministic post-rain delay state from vendor transition/settings."""
    runtime = _rain_runtime(coordinator)
    now = time.time()
    delay_enabled, configured_minutes = _rain_delay_config(snapshot)
    rain_state = weather.get("rain_state")
    vendor_fresh = weather.get("vendor_fresh") is True

    # Only a fresh vendor decision may create the 1 -> 0 transition. Stale or
    # missing weather must never invent a rain-clear edge.
    if vendor_fresh and rain_state is True:
        _clear_rain_delay(coordinator)
        if runtime.get("last_rain_state") is not True:
            _mark_rain_runtime_dirty(coordinator)
        runtime["last_rain_state"] = True
        runtime["last_rain_state_at"] = now
    elif vendor_fresh and rain_state is False:
        if runtime.get("last_rain_state") is True:
            if delay_enabled and configured_minutes > 0:
                _start_rain_delay(
                    coordinator,
                    minutes=configured_minutes,
                    now=now,
                )
            else:
                _clear_rain_delay(coordinator)
        if runtime.get("last_rain_state") is not False:
            _mark_rain_runtime_dirty(coordinator)
        runtime["last_rain_state"] = False
        runtime["last_rain_state_at"] = now

    # Turning the vendor delay mode off explicitly cancels a locally retained
    # cooldown. Changing the configured duration while a cooldown is already
    # running does not move its deadline; the value at the 1 -> 0 edge wins.
    if runtime.get("delay_until") is not None and not delay_enabled:
        _clear_rain_delay(coordinator)

    try:
        delay_until = float(runtime.get("delay_until"))
    except (TypeError, ValueError):
        delay_until = 0.0

    if delay_until > 0 and delay_until <= now:
        _clear_rain_delay(coordinator)
        delay_until = 0.0

    delay_active = delay_until > now
    remaining_seconds = (
        max(0, int(math.ceil(delay_until - now)))
        if delay_active
        else 0
    )
    remaining_minutes = (
        max(1, int(math.ceil(remaining_seconds / 60.0)))
        if delay_active
        else 0
    )

    if delay_active:
        reasons = list(weather.get("hold_reasons") or [])
        if "rain_delay" not in reasons:
            reasons.append("rain_delay")
        weather["hold_reasons"] = reasons
        weather["hold_active"] = True
        # A deterministic countdown derived from a previously fresh 1 -> 0 edge
        # remains safe for scheduler/automation use until its known deadline.
        weather["fresh"] = True
        if len(reasons) == 1:
            weather["state"] = "rain_delay"
            weather["label"] = "Rain delay"
            weather["hold_reason"] = "rain_delay"
        else:
            weather["state"] = "weather_delay"
            weather["label"] = "Weather delay"
            if not weather.get("hold_reason"):
                weather["hold_reason"] = reasons[0]
        vendor_source = weather.get("vendor_source")
        weather["source"] = (
            f"{vendor_source}+derived_rain_delay"
            if vendor_source
            else "derived_rain_delay"
        )

    weather["rain_last_fresh_state"] = runtime.get("last_rain_state")
    weather["rain_last_fresh_state_at"] = _iso_timestamp(
        runtime.get("last_rain_state_at")
    )
    weather["rain_delay_enabled"] = delay_enabled
    weather["rain_delay_configured_minutes"] = configured_minutes
    weather["rain_delay_active"] = delay_active
    weather["rain_delay_started_at"] = _iso_timestamp(
        runtime.get("delay_started_at")
    )
    weather["rain_delay_until"] = _iso_timestamp(delay_until) if delay_active else None
    weather["rain_delay_remaining_seconds"] = remaining_seconds
    weather["rain_delay_remaining_minutes"] = remaining_minutes
    return weather


def _decorate_snapshot(
    coordinator: NavimowCoordinator,
    snapshot: dict[str, Any],
) -> None:
    raw = coordinator._raw_cache.get("vehicle_weather")  # noqa: SLF001
    weather = normalize_vendor_weather(raw, age_s=_endpoint_age(coordinator))
    weather = _compose_rain_delay(coordinator, snapshot, weather)
    snapshot["vendor_weather"] = weather
    snapshot["weather_state"] = weather["state"]
    snapshot["weather_state_source"] = weather["source"]
    snapshot["weather_state_age"] = weather["age_s"]
    snapshot["weather_state_fresh"] = weather["fresh"]
    snapshot["weather_vendor_fresh"] = weather["vendor_fresh"]
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
    snapshot["rain_last_fresh_state"] = weather["rain_last_fresh_state"]
    snapshot["rain_last_fresh_state_at"] = weather["rain_last_fresh_state_at"]
    snapshot["rain_delay_enabled"] = weather["rain_delay_enabled"]
    snapshot["rain_delay_configured_minutes"] = weather[
        "rain_delay_configured_minutes"
    ]
    snapshot["rain_delay_active"] = weather["rain_delay_active"]
    snapshot["rain_delay_started_at"] = weather["rain_delay_started_at"]
    snapshot["rain_delay_until"] = weather["rain_delay_until"]
    snapshot["rain_delay_remaining_seconds"] = weather[
        "rain_delay_remaining_seconds"
    ]
    snapshot["rain_delay_remaining_minutes"] = weather[
        "rain_delay_remaining_minutes"
    ]

    # Home Assistant's lawn_mower activity remains physical/canonical. A fresh
    # vendor/derived weather decision composes only the human-facing status layer.
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


def _persisted_rain_runtime(coordinator: NavimowCoordinator) -> dict[str, Any]:
    runtime = _rain_runtime(coordinator)
    return {
        "last_rain_state": runtime.get("last_rain_state"),
        "last_rain_state_at": runtime.get("last_rain_state_at"),
        "delay_started_at": runtime.get("delay_started_at"),
        "delay_until": runtime.get("delay_until"),
        "delay_minutes": runtime.get("delay_minutes") or 0,
    }


def install_weather_state_semantics() -> None:
    """Install bounded vendor weather polling and post-rain delay semantics."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_fetch_blocking = NavimowCoordinator._fetch_blocking
    original_load_state = NavimowCoordinator.async_load_persistent_state
    original_state_store_data = NavimowCoordinator._state_store_data
    original_async_update_data = NavimowCoordinator._async_update_data

    def fetch_blocking(self: NavimowCoordinator) -> dict[str, Any]:
        snapshot = original_fetch_blocking(self)
        _refresh_vendor_weather(self, snapshot)
        return snapshot

    async def async_load_persistent_state(self: NavimowCoordinator) -> None:
        await original_load_state(self)
        runtime = _rain_runtime(self)
        runtime["last_rain_state"] = None
        runtime["last_rain_state_at"] = None
        try:
            cached = await self._state_store.async_load()  # noqa: SLF001
        except Exception:  # noqa: BLE001
            cached = None
        row = (
            cached.get(_WEATHER_RUNTIME_STORE_KEY)
            if isinstance(cached, dict)
            else None
        )
        now = time.time()
        if isinstance(row, dict):
            try:
                until = float(row.get("delay_until") or 0)
                started = float(row.get("delay_started_at") or 0)
                minutes = int(row.get("delay_minutes") or 0)
                last_state = row.get("last_rain_state")
                last_state_at = float(row.get("last_rain_state_at") or 0)
            except (TypeError, ValueError):
                until, started, minutes = 0.0, 0.0, 0
                last_state, last_state_at = None, 0.0
            if last_state in (True, False) and last_state_at > 0:
                runtime["last_rain_state"] = last_state
                runtime["last_rain_state_at"] = last_state_at
            if until > now and started > 0 and minutes > 0:
                runtime["delay_started_at"] = started
                runtime["delay_until"] = until
                runtime["delay_minutes"] = minutes
            else:
                runtime["delay_started_at"] = None
                runtime["delay_until"] = None
                runtime["delay_minutes"] = 0

    def state_store_data(
        self: NavimowCoordinator,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = original_state_store_data(self, snapshot)
        data[_WEATHER_RUNTIME_STORE_KEY] = _persisted_rain_runtime(self)
        return data

    async def async_update_data(self: NavimowCoordinator) -> dict[str, Any]:
        snapshot = await original_async_update_data(self)
        if getattr(self, "_rain_delay_persist_dirty", False):
            try:
                await self._state_store.async_save(  # noqa: SLF001
                    self._state_store_data(snapshot)  # noqa: SLF001
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Rain-delay checkpoint failed", exc_info=True)
            else:
                self._rain_delay_persist_dirty = False  # type: ignore[attr-defined]  # noqa: SLF001
        return snapshot

    NavimowCoordinator._fetch_blocking = fetch_blocking  # type: ignore[method-assign]
    NavimowCoordinator.async_load_persistent_state = async_load_persistent_state  # type: ignore[method-assign]
    NavimowCoordinator._state_store_data = state_store_data  # type: ignore[method-assign]
    NavimowCoordinator._async_update_data = async_update_data  # type: ignore[method-assign]
    _INSTALLED = True
