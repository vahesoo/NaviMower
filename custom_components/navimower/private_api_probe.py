"""Explicit read-only private-cloud probes for controlled beta field testing.

This module intentionally exposes only a fixed allow-list of non-mutating
vendor endpoints. It is separate from normal Home Assistant diagnostics and
from the production polling model: probes run only when the user explicitly
invokes the development action.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Callable

DOMAIN = "navimower"
SERVICE_PROBE_PRIVATE_API = "probe_private_api"
PROBE_NAMES = ("trail", "reports", "terrain", "weather", "rtk")
PROBE_CHOICES = ("all", *PROBE_NAMES)

_REDACTED = "**REDACTED**"
_SENSITIVE_NORMALIZED_KEYS = {"rtkaccount", "rtkpassword"}


def _normalized_key(value: Any) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _sanitize_probe_value(value: Any) -> Any:
    """Keep discovery structure while suppressing RTK service credentials."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if _normalized_key(key) in _SENSITIVE_NORMALIZED_KEYS:
                out[str(key)] = _REDACTED
            else:
                out[str(key)] = _sanitize_probe_value(item)
        return out
    if isinstance(value, list):
        return [_sanitize_probe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_probe_value(item) for item in value]
    return deepcopy(value)


def _capture(getter: Callable[[], Any]) -> dict[str, Any]:
    try:
        return {"ok": True, "data": _sanitize_probe_value(getter())}
    except Exception as err:  # noqa: BLE001 - discovery must retain partial results.
        return {"ok": False, "error": repr(err)}


def _call_variants(
    client: Any,
    path: str,
    requests: list[dict[str, Any]],
) -> dict[str, Any]:
    """Try bounded read-only request variants until one succeeds."""
    attempts: list[dict[str, Any]] = []
    selected_attempt: int | None = None
    for index, request in enumerate(requests):
        captured = _capture(lambda request=request: client.call(path, request))
        attempt = {"request": deepcopy(request), **captured}
        attempts.append(attempt)
        if captured["ok"]:
            selected_attempt = index
            break
    return {
        "path": path,
        "ok": selected_attempt is not None,
        "selected_attempt": selected_attempt,
        "attempts": attempts,
    }


def _selected_data(result: dict[str, Any]) -> Any:
    index = result.get("selected_attempt")
    attempts = result.get("attempts") or []
    if isinstance(index, int) and 0 <= index < len(attempts):
        return attempts[index].get("data")
    return None


def _mower_id_variants(sn: str, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    suffix = dict(extra or {})
    return [
        {"vehicle_sn": sn, **suffix},
        {"sn": sn, **suffix},
    ]


def _skipped(path: str, reason: str) -> dict[str, Any]:
    return {
        "path": path,
        "ok": False,
        "skipped": True,
        "reason": reason,
        "selected_attempt": None,
        "attempts": [],
    }


def _probe_trail(client: Any, sn: str) -> dict[str, Any]:
    time_result = _call_variants(
        client,
        "/vehicle/trail/get-path-info-time",
        [{"vehicle_sn": sn}],
    )
    rows = _selected_data(time_result)
    partition_ids: list[int] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict) or row.get("partitionId") is None:
                continue
            try:
                partition_id = int(row["partitionId"])
            except (TypeError, ValueError):
                continue
            if partition_id not in partition_ids:
                partition_ids.append(partition_id)

    path = "/vehicle/trail/get-path-info-data-compress"
    if partition_ids:
        path_result = _call_variants(
            client,
            path,
            [{"vehicle_sn": sn, "partitionList": partition_ids}],
        )
    else:
        path_result = _skipped(path, "No partitionId values returned by get-path-info-time")
    return {
        "path_info_time": time_result,
        "partition_ids": partition_ids,
        "path_info_data_compress": path_result,
    }


def _probe_reports(client: Any, sn: str) -> dict[str, Any]:
    language = str(getattr(client, "_language", "en") or "en")
    today = datetime.now().astimezone().date()
    query_time = f"{today.year}-{today.month}-{today.day}"
    base = {"vehicle_sn": sn, "language": language}
    result: dict[str, Any] = {
        "query_time": query_time,
        "vehicle_main_report": _call_variants(
            client,
            "/vehicle/report/vehicle-main-report",
            [base],
        ),
    }
    for query_type, label in ((1, "day"), (2, "week"), (3, "month")):
        result[label] = _call_variants(
            client,
            "/vehicle/report/get-day-week-month-data",
            [{**base, "query_type": query_type, "query_time": query_time}],
        )
    return result


def _probe_terrain(client: Any, sn: str) -> dict[str, Any]:
    return {
        "dynamic_map": _call_variants(
            client,
            "/mowerbot/map/queryDynamicsMap",
            _mower_id_variants(sn),
        ),
        "iot_file_type_2": _call_variants(
            client,
            "/mowerbot/vehicle/common/get-iot-file",
            [
                {"vehicle_sn": sn, "type": 2},
                {"sn": sn, "type": 2},
                {"vehicle_sn": sn, "type": "2"},
                {"sn": sn, "type": "2"},
            ],
        ),
    }


def _probe_weather(client: Any, sn: str) -> dict[str, Any]:
    return _call_variants(
        client,
        "/vehicle/vehicle/get-vehicle-weather",
        _mower_id_variants(sn),
    )


def _probe_rtk(client: Any, sn: str) -> dict[str, Any]:
    return _call_variants(
        client,
        "/mowerbot/vehicle/rtk/queryRtkService",
        _mower_id_variants(sn),
    )


def _build_probe_document(coordinator: Any, probe: str) -> dict[str, Any]:
    if probe not in PROBE_CHOICES:
        raise ValueError(f"Unsupported probe: {probe}")
    client = coordinator.client
    sn = str(coordinator.sn)
    selected = PROBE_NAMES if probe == "all" else (probe,)
    handlers: dict[str, Callable[[Any, str], Any]] = {
        "trail": _probe_trail,
        "reports": _probe_reports,
        "terrain": _probe_terrain,
        "weather": _probe_weather,
        "rtk": _probe_rtk,
    }
    return {
        "format": "navimower-private-api-probe-v1",
        "created_utc": datetime.now(UTC).isoformat(),
        "warning": (
            "EXPLICIT DEVELOPMENT PROBE. Contains exact mower identifiers, request "
            "parameters and potentially exact map/location data. RTK account/password "
            "values are redacted. Do not publish this file without review."
        ),
        "source": "navimower.probe_private_api",
        "entry_id": coordinator.entry.entry_id,
        "vehicle_sn": sn,
        "vehicle_type": coordinator.vehicle_type,
        "probe": probe,
        "probes": {name: handlers[name](client, sn) for name in selected},
    }


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, default=repr),
        encoding="utf-8",
    )


async def async_run_private_api_probe(hass: Any, coordinator: Any, probe: str) -> str:
    """Run one explicit probe batch off the event loop and write its raw result."""
    document = await hass.async_add_executor_job(_build_probe_document, coordinator, probe)
    now = datetime.now(UTC)
    folder = Path(hass.config.path("navimower_diagnostics", "probes"))
    stamp = now.strftime("%Y%m%d_%H%M%S")
    path = folder / f"navimower_probe_{probe}_{stamp}.json"
    latest = folder / f"navimower_probe_{probe}_latest.json"
    await hass.async_add_executor_job(_write_json, path, document)
    await hass.async_add_executor_job(_write_json, latest, document)
    return str(path)


def async_setup_private_api_probe(hass: Any) -> None:
    """Register the bounded development action once."""
    if hass.services.has_service(DOMAIN, SERVICE_PROBE_PRIVATE_API):
        return

    import voluptuous as vol
    from homeassistant.components import persistent_notification
    from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
    from homeassistant.helpers import config_validation as cv
    from homeassistant.helpers import device_registry as dr

    schema = vol.Schema(
        {
            vol.Optional("device_id"): cv.string,
            vol.Optional("probe", default="all"): vol.In(PROBE_CHOICES),
        }
    )

    def resolve_coordinator(call: Any) -> Any:
        store = hass.data.get(DOMAIN) or {}
        coordinators = [
            value
            for key, value in store.items()
            if not str(key).startswith("_")
            and hasattr(value, "entry")
            and hasattr(value, "client")
        ]
        device_id = call.data.get("device_id")
        if device_id:
            device = dr.async_get(hass).async_get(device_id)
            if device:
                for entry_id in device.config_entries:
                    if entry_id in store:
                        return store[entry_id]
            raise ServiceValidationError("device_id is not a Navimower mower")
        if len(coordinators) == 1:
            return coordinators[0]
        raise ServiceValidationError(
            "Multiple Navimow mowers configured: pass device_id to choose one"
        )

    async def handle(call: Any) -> None:
        coordinator = resolve_coordinator(call)
        probe = str(call.data.get("probe") or "all")
        try:
            path = await async_run_private_api_probe(hass, coordinator, probe)
        except Exception as err:  # noqa: BLE001 - surface field-test failures in HA.
            raise HomeAssistantError(f"Navimower private API probe failed: {err}") from err
        persistent_notification.async_create(
            hass,
            (
                f"Navimower private API probe `{probe}` completed.\n\n"
                f"File: `{path}`\n\n"
                "This development file can contain mower identifiers and exact "
                "map/location data. RTK account/password values are redacted."
            ),
            title="Navimower private API probe",
            notification_id=f"navimower_private_api_probe_{coordinator.entry.entry_id}",
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_PROBE_PRIVATE_API,
        handle,
        schema=schema,
    )
