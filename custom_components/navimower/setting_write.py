"""Transactional private-cloud setting writes with delayed readback.

Navimow applies many settings through two blocking calls: an immediate command
sent to the mower and a private-cloud persistence write. The private cloud is
eventually consistent, so refreshing ``set_list`` between those calls can
briefly republish the previous value.

This helper runs every operation in one executor job, updates the integration's
last-good settings cache only after all writes were acknowledged, and forces one
fresh ``set_list`` read after a short propagation delay. Normal coordinator
polls continue for battery, position and progress while reusing the write-through
settings cache during that delay.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
import logging
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)

SETTING_READBACK_DELAY_SECONDS = 15.0
SETTING_PERSISTENCE_DELAY_SECONDS = 75.0

SettingOperation = tuple[Callable[..., Any], tuple[Any, ...]]


def _set_list_status(coordinator: Any) -> dict[str, Any]:
    """Return a complete endpoint-status row for ``set_list``."""
    return coordinator._endpoint_status.setdefault(  # noqa: SLF001
        "set_list",
        {
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "last_attempt_mono": None,
            "last_success_mono": None,
            "last_error": None,
            "last_attempt_utc": None,
            "last_success_utc": None,
            "last_error_utc": None,
        },
    )


def _value_kind(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "mapping"
    if isinstance(value, (list, tuple)):
        return "sequence"
    return type(value).__name__


def _numeric_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (TypeError, ValueError):
            return None
    return None


def _values_equivalent(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _values_equivalent(value, actual.get(key))
            for key, value in expected.items()
        )
    if isinstance(expected, (list, tuple)):
        return (
            isinstance(actual, (list, tuple))
            and len(expected) == len(actual)
            and all(_values_equivalent(a, b) for a, b in zip(expected, actual))
        )
    if isinstance(expected, str) and isinstance(actual, str):
        if expected.strip().casefold() == actual.strip().casefold():
            return True
    left = _numeric_value(expected)
    right = _numeric_value(actual)
    if left is not None and right is not None:
        return left == right
    return expected == actual


def _finish_verification(coordinator: Any, cache_values: dict[str, Any]) -> None:
    set_list = dict(getattr(coordinator, "_raw_cache", {}).get("set_list") or {})
    rows: dict[str, Any] = {}
    failed: list[str] = []
    for key, expected in cache_values.items():
        present = key in set_list
        actual = set_list.get(key)
        confirmed = present and _values_equivalent(expected, actual)
        status = "confirmed" if confirmed else ("missing" if not present else "reverted")
        if not confirmed:
            failed.append(str(key))
        rows[str(key)] = {
            "status": status,
            "requested_type": _value_kind(expected),
            "readback_type": _value_kind(actual) if present else None,
        }
    previous = getattr(coordinator, "_setting_write_verification", {}) or {}
    coordinator._setting_write_verification = {  # noqa: SLF001
        "status": "confirmed" if not failed else "reverted",
        "requested_at_utc": previous.get("requested_at_utc"),
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "keys": rows,
    }
    if failed:
        _LOGGER.warning(
            "Navimow setting persistence check failed for %s",
            ", ".join(failed),
        )


async def _publish_write_through_cache(
    coordinator: Any, cache_values: dict[str, Any]
) -> None:
    """Publish acknowledged values without waiting for stale cloud readback."""
    raw_cache = coordinator._raw_cache  # noqa: SLF001
    set_list = dict(raw_cache.get("set_list") or {})
    set_list.update(cache_values)
    raw_cache["set_list"] = set_list

    # Reuse the coordinator's one authoritative parser so raw-backed and parsed
    # entities (switch/select/number/time/schedule) all receive the same values.
    raw_snapshot = dict(raw_cache)
    raw_snapshot["set_list"] = dict(set_list)
    parsed = await coordinator.hass.async_add_executor_job(
        coordinator._parse, raw_snapshot  # noqa: SLF001
    )

    snapshot = dict(coordinator.data or parsed)
    for key in (
        "settings",
        "schedule",
        "next_mow",
        "cut_height",
        "cutting_height_mm",
        "cutting_height_supported",
    ):
        if key in parsed:
            snapshot[key] = parsed[key]
    raw = dict(snapshot.get("raw") or {})
    raw["set_list"] = dict((parsed.get("raw") or {}).get("set_list") or set_list)
    snapshot["raw"] = raw
    coordinator.async_set_updated_data(snapshot)


def _schedule_readback(
    coordinator: Any,
    delay: float,
    cache_values: dict[str, Any],
) -> None:
    """Refresh once for propagation, then verify persistence later."""
    previous = getattr(coordinator, "_setting_readback_task", None)
    if previous is not None and not previous.done():
        previous.cancel()

    async def _refresh_settings() -> None:
        status = _set_list_status(coordinator)
        status["last_attempt_mono"] = None
        status["last_attempt_utc"] = None
        await coordinator.async_request_refresh()

    async def _readback() -> None:
        try:
            await asyncio.sleep(delay)
            if getattr(coordinator, "_shutdown_complete", False):
                return
            await _refresh_settings()
            remaining = max(0.0, SETTING_PERSISTENCE_DELAY_SECONDS - delay)
            if remaining:
                await asyncio.sleep(remaining)
            if getattr(coordinator, "_shutdown_complete", False):
                return
            await _refresh_settings()
            _finish_verification(coordinator, cache_values)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.debug("Delayed Navimow settings readback failed", exc_info=True)
        finally:
            current = getattr(coordinator, "_setting_readback_task", None)
            if current is asyncio.current_task():
                coordinator._setting_readback_task = None  # noqa: SLF001

    coordinator._setting_readback_task = coordinator.hass.async_create_task(  # noqa: SLF001
        _readback(),
        f"Navimower settings readback {coordinator.entry.entry_id}",
    )


async def async_write_settings(
    coordinator: Any,
    *,
    operations: Sequence[SettingOperation],
    cache_values: dict[str, Any],
    readback_delay: float = SETTING_READBACK_DELAY_SECONDS,
) -> Any:
    """Run setting operations atomically, then confirm them after a delay.

    No coordinator refresh occurs between operations. Once every blocking call
    returns successfully, the acknowledged values are written through to the
    local ``set_list`` cache. A forced cloud readback replaces that cache after
    ``readback_delay`` seconds.
    """
    if not operations:
        return None

    status = _set_list_status(coordinator)
    now = time.monotonic()
    # A concurrent normal poll may continue, but it must not fetch an old
    # ``set_list`` while this transaction is in flight.
    status["last_attempt_mono"] = now
    status["last_attempt_utc"] = datetime.now(UTC).isoformat()

    def _run_operations() -> list[Any]:
        return [func(*args) for func, args in operations]

    try:
        results = await coordinator.hass.async_add_executor_job(_run_operations)
    except Exception:
        # Let the next normal refresh establish the real state after a failed
        # partial transaction instead of retaining the temporary TTL hold.
        status["last_attempt_mono"] = None
        status["last_attempt_utc"] = None
        raise

    coordinator._persist_session()  # noqa: SLF001
    coordinator._setting_write_verification = {  # noqa: SLF001
        "status": "pending",
        "requested_at_utc": datetime.now(UTC).isoformat(),
        "checked_at_utc": None,
        "keys": {
            str(key): {
                "status": "pending",
                "requested_type": _value_kind(value),
                "readback_type": None,
            }
            for key, value in cache_values.items()
        },
    }
    await _publish_write_through_cache(coordinator, cache_values)

    # Reset the TTL from the completed transaction, not from its start.
    status["last_attempt_mono"] = time.monotonic()
    status["last_attempt_utc"] = datetime.now(UTC).isoformat()
    _schedule_readback(
        coordinator,
        max(0.0, float(readback_delay)),
        dict(cache_values),
    )
    return results[-1] if results else None
