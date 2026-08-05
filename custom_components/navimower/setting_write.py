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


def _schedule_readback(coordinator: Any, delay: float) -> None:
    """Force one fresh ``set_list`` read after cloud propagation time."""
    previous = getattr(coordinator, "_setting_readback_task", None)
    if previous is not None and not previous.done():
        previous.cancel()

    async def _readback() -> None:
        try:
            await asyncio.sleep(delay)
            if getattr(coordinator, "_shutdown_complete", False):
                return
            status = _set_list_status(coordinator)
            # Bypass the normal 30/60-second endpoint TTL for this confirmation.
            status["last_attempt_mono"] = None
            status["last_attempt_utc"] = None
            await coordinator.async_request_refresh()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - normal polling remains the fallback.
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
    await _publish_write_through_cache(coordinator, cache_values)

    # Reset the TTL from the completed transaction, not from its start.
    status["last_attempt_mono"] = time.monotonic()
    status["last_attempt_utc"] = datetime.now(UTC).isoformat()
    _schedule_readback(coordinator, max(0.0, float(readback_delay)))
    return results[-1] if results else None
