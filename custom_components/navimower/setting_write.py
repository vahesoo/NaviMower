"""Transactional private-cloud setting writes with delayed readback.

Navimow applies many settings through two blocking calls: an immediate command
sent to the mower and a private-cloud persistence write. The private cloud is
eventually consistent, so refreshing ``set_list`` between those calls can
briefly republish the previous value.

This helper owns the write transaction, write-through cache, device-command
outcome probe and per-setting persistence verification. Readback tasks are keyed
by setting so changing an unrelated setting cannot cancel an earlier check.
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
SETTING_COMMAND_STATUS_RETRY_DELAYS_SECONDS = (1.0, 3.0, 6.0)

SettingOperation = tuple[Callable[..., Any], tuple[Any, ...]]

_COMMAND_NUMBER_KEYS = (
    "cmd_num",
    "cmdNum",
    "command_num",
    "commandNum",
    "command_number",
    "commandNumber",
)
_COMMAND_STATUS_KEYS = {
    "status",
    "state",
    "result",
    "code",
    "resultcode",
    "result_code",
    "errorcode",
    "error_code",
    "success",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


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


def _command_number(value: Any) -> str | None:
    """Extract a vendor command number without retaining the raw response."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text or None
    if isinstance(value, dict):
        for key in _COMMAND_NUMBER_KEYS:
            if key in value:
                found = _command_number(value.get(key))
                if found is not None:
                    return found
        for nested in value.values():
            if isinstance(nested, (dict, list, tuple)):
                found = _command_number(nested)
                if found is not None:
                    return found
    if isinstance(value, (list, tuple)):
        for nested in value:
            found = _command_number(nested)
            if found is not None:
                return found
    return None


def _device_command_result(
    operations: Sequence[SettingOperation], results: Sequence[Any]
) -> tuple[bool, str | None]:
    """Return whether this write used a mower command and its command number."""
    used = False
    for (func, _args), result in zip(operations, results):
        if getattr(func, "__name__", "") != "send_setting_device":
            continue
        used = True
        number = _command_number(result)
        if number is not None:
            return True, number
    return used, None


def _status_fields(
    value: Any,
    *,
    prefix: str = "",
    depth: int = 0,
    out: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract only small status-like scalars from a command response."""
    result = out if out is not None else {}
    if depth > 3 or len(result) >= 12:
        return result
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            normalized = key_text.casefold()
            if normalized in _COMMAND_STATUS_KEYS:
                if isinstance(nested, (bool, int, float)):
                    result[path] = nested
                elif isinstance(nested, str) and len(nested) <= 32:
                    result[path] = nested
            if isinstance(nested, (dict, list, tuple)):
                _status_fields(nested, prefix=path, depth=depth + 1, out=result)
            if len(result) >= 12:
                break
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value[:8]):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            _status_fields(nested, prefix=path, depth=depth + 1, out=result)
            if len(result) >= 12:
                break
    return result


def _command_response_summary(value: Any) -> dict[str, Any]:
    """Return a privacy-safe structural summary of /set/response."""
    summary: dict[str, Any] = {"response_type": _value_kind(value)}
    if isinstance(value, dict):
        summary["top_level_keys"] = sorted(str(key) for key in value)[:20]
    fields = _status_fields(value)
    if fields:
        summary["status_fields"] = fields
    return summary


def _verification_store(coordinator: Any) -> dict[str, Any]:
    store = getattr(coordinator, "_setting_write_verification", None)
    if not isinstance(store, dict) or store.get("schema_version") != 2:
        store = {
            "schema_version": 2,
            "status": "idle",
            "last_updated_utc": None,
            "keys": {},
        }
        coordinator._setting_write_verification = store  # noqa: SLF001
    if not isinstance(store.get("keys"), dict):
        store["keys"] = {}
    return store


def _update_verification_summary(coordinator: Any) -> None:
    store = _verification_store(coordinator)
    rows = list(store["keys"].values())
    statuses = {
        str(row.get("status"))
        for row in rows
        if isinstance(row, dict) and row.get("status")
    }
    if not statuses:
        overall = "idle"
    elif statuses & {"reverted", "missing"}:
        overall = "attention"
    elif "pending" in statuses:
        overall = "pending"
    elif statuses == {"confirmed"}:
        overall = "confirmed"
    else:
        overall = "mixed"
    store["status"] = overall
    store["last_updated_utc"] = _utc_now()


def _set_pending_verification(
    coordinator: Any,
    cache_values: dict[str, Any],
    *,
    device_command_used: bool,
    command_number: str | None,
) -> None:
    store = _verification_store(coordinator)
    requested_at = _utc_now()
    for key, expected in cache_values.items():
        if not device_command_used:
            command_outcome = "not_used"
        elif command_number is None:
            command_outcome = "no_command_number"
        else:
            command_outcome = "pending"
        store["keys"][str(key)] = {
            "status": "pending",
            "requested_at_utc": requested_at,
            "checked_at_utc": None,
            "requested_type": _value_kind(expected),
            "readback_type": None,
            "readback_15s": None,
            "persistence_75s": None,
            "device_command": {
                "used": device_command_used,
                "command_number_received": command_number is not None,
                "outcome": command_outcome,
            },
        }
    _update_verification_summary(coordinator)


def _record_readback(
    coordinator: Any,
    key: str,
    expected: Any,
    *,
    phase: str,
) -> str:
    set_list = dict(getattr(coordinator, "_raw_cache", {}).get("set_list") or {})
    present = key in set_list
    actual = set_list.get(key)
    confirmed = present and _values_equivalent(expected, actual)
    status = "confirmed" if confirmed else ("missing" if not present else "reverted")
    checked_at = _utc_now()

    store = _verification_store(coordinator)
    row = store["keys"].get(str(key))
    if not isinstance(row, dict):
        return status
    row[phase] = {
        "status": status,
        "readback_type": _value_kind(actual) if present else None,
        "checked_at_utc": checked_at,
    }
    row["readback_type"] = _value_kind(actual) if present else None
    if phase == "persistence_75s":
        row["status"] = status
        row["checked_at_utc"] = checked_at
    elif status != "confirmed":
        row["status"] = status
    _update_verification_summary(coordinator)
    return status


def _update_device_command(
    coordinator: Any,
    key: str,
    update: dict[str, Any],
) -> None:
    store = _verification_store(coordinator)
    row = store["keys"].get(str(key))
    if not isinstance(row, dict):
        return
    command = row.get("device_command")
    if not isinstance(command, dict):
        command = {}
        row["device_command"] = command
    command.update(update)
    command["checked_at_utc"] = _utc_now()
    _update_verification_summary(coordinator)


async def _publish_write_through_cache(
    coordinator: Any, cache_values: dict[str, Any]
) -> None:
    """Publish acknowledged values without waiting for stale cloud readback."""
    raw_cache = coordinator._raw_cache  # noqa: SLF001
    set_list = dict(raw_cache.get("set_list") or {})
    set_list.update(cache_values)
    raw_cache["set_list"] = set_list

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


async def _refresh_settings(coordinator: Any) -> None:
    status = _set_list_status(coordinator)
    status["last_attempt_mono"] = None
    status["last_attempt_utc"] = None
    await coordinator.async_request_refresh()


def _schedule_readback(
    coordinator: Any,
    delay: float,
    cache_values: dict[str, Any],
) -> None:
    """Schedule independent short and persistence readbacks per setting key."""
    tasks = getattr(coordinator, "_setting_readback_tasks", None)
    if not isinstance(tasks, dict):
        tasks = {}
        coordinator._setting_readback_tasks = tasks  # noqa: SLF001

    for raw_key, expected in cache_values.items():
        key = str(raw_key)
        previous = tasks.get(key)
        if previous is not None and not previous.done():
            previous.cancel()

        async def _readback(
            *,
            verify_key: str = key,
            verify_expected: Any = expected,
        ) -> None:
            try:
                await asyncio.sleep(delay)
                if getattr(coordinator, "_shutdown_complete", False):
                    return
                await _refresh_settings(coordinator)
                short_status = _record_readback(
                    coordinator,
                    verify_key,
                    verify_expected,
                    phase="readback_15s",
                )
                if short_status != "confirmed":
                    _LOGGER.warning(
                        "Navimow setting %s reverted or disappeared after short readback",
                        verify_key,
                    )

                remaining = max(0.0, SETTING_PERSISTENCE_DELAY_SECONDS - delay)
                if remaining:
                    await asyncio.sleep(remaining)
                if getattr(coordinator, "_shutdown_complete", False):
                    return
                await _refresh_settings(coordinator)
                final_status = _record_readback(
                    coordinator,
                    verify_key,
                    verify_expected,
                    phase="persistence_75s",
                )
                if final_status != "confirmed":
                    _LOGGER.warning(
                        "Navimow setting persistence check failed for %s",
                        verify_key,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.debug(
                    "Delayed Navimow settings readback failed for %s",
                    verify_key,
                    exc_info=True,
                )
                store = _verification_store(coordinator)
                row = store["keys"].get(verify_key)
                if isinstance(row, dict):
                    row["readback_error_type"] = type(err).__name__
                    _update_verification_summary(coordinator)
            finally:
                current = tasks.get(verify_key)
                if current is asyncio.current_task():
                    tasks.pop(verify_key, None)

        tasks[key] = coordinator.hass.async_create_task(
            _readback(),
            f"Navimower setting readback {coordinator.entry.entry_id} {key}",
        )


def _schedule_command_status(
    coordinator: Any,
    command_number: str | None,
    keys: Sequence[str],
) -> None:
    """Probe the mower-side command result without retaining raw vendor data."""
    if command_number is None:
        return
    command_status = getattr(coordinator.client, "command_status", None)
    if not callable(command_status):
        for key in keys:
            _update_device_command(coordinator, key, {"outcome": "unsupported"})
        return

    tasks = getattr(coordinator, "_setting_command_tasks", None)
    if not isinstance(tasks, dict):
        tasks = {}
        coordinator._setting_command_tasks = tasks  # noqa: SLF001

    for raw_key in keys:
        key = str(raw_key)
        previous = tasks.get(key)
        if previous is not None and not previous.done():
            previous.cancel()

        async def _check(*, verify_key: str = key) -> None:
            last_error_type: str | None = None
            saw_empty_response = False
            try:
                for retry_delay in SETTING_COMMAND_STATUS_RETRY_DELAYS_SECONDS:
                    await asyncio.sleep(retry_delay)
                    if getattr(coordinator, "_shutdown_complete", False):
                        return
                    try:
                        response = await coordinator.hass.async_add_executor_job(
                            command_status,
                            coordinator.sn,
                            command_number,
                        )
                    except Exception as err:
                        last_error_type = type(err).__name__
                        continue

                    summary = _command_response_summary(response)
                    if response:
                        _update_device_command(
                            coordinator,
                            verify_key,
                            {"outcome": "response_received", **summary},
                        )
                        return
                    saw_empty_response = True

                if saw_empty_response:
                    _update_device_command(
                        coordinator,
                        verify_key,
                        {
                            "outcome": "empty_response",
                            "response_type": "mapping",
                            "top_level_keys": [],
                        },
                    )
                else:
                    _update_device_command(
                        coordinator,
                        verify_key,
                        {
                            "outcome": "query_error",
                            "error_type": last_error_type,
                        },
                    )
            except asyncio.CancelledError:
                raise
            finally:
                current = tasks.get(verify_key)
                if current is asyncio.current_task():
                    tasks.pop(verify_key, None)

        tasks[key] = coordinator.hass.async_create_task(
            _check(),
            f"Navimower setting command status {coordinator.entry.entry_id} {key}",
        )


async def async_write_settings(
    coordinator: Any,
    *,
    operations: Sequence[SettingOperation],
    cache_values: dict[str, Any],
    readback_delay: float = SETTING_READBACK_DELAY_SECONDS,
) -> Any:
    """Run setting operations atomically and verify each setting independently."""
    if not operations:
        return None

    status = _set_list_status(coordinator)
    now = time.monotonic()
    status["last_attempt_mono"] = now
    status["last_attempt_utc"] = _utc_now()

    def _run_operations() -> list[Any]:
        return [func(*args) for func, args in operations]

    try:
        results = await coordinator.hass.async_add_executor_job(_run_operations)
    except Exception:
        status["last_attempt_mono"] = None
        status["last_attempt_utc"] = None
        raise

    device_command_used, command_number = _device_command_result(operations, results)

    coordinator._persist_session()  # noqa: SLF001
    _set_pending_verification(
        coordinator,
        cache_values,
        device_command_used=device_command_used,
        command_number=command_number,
    )
    await _publish_write_through_cache(coordinator, cache_values)

    status["last_attempt_mono"] = time.monotonic()
    status["last_attempt_utc"] = _utc_now()

    keys = [str(key) for key in cache_values]
    _schedule_command_status(coordinator, command_number, keys)
    _schedule_readback(
        coordinator,
        max(0.0, float(readback_delay)),
        dict(cache_values),
    )
    return results[-1] if results else None
