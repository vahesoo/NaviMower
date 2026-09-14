"""Explicit read-only private-cloud probes for controlled beta field testing.

This module intentionally exposes only a fixed allow-list of non-mutating
vendor endpoints. It is separate from normal Home Assistant diagnostics and
from the production polling model: probes run only when the user explicitly
invokes the development action.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from datetime import UTC, datetime
import gzip
import hashlib
import json
from pathlib import Path
import string
from typing import Any, Callable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile
import zlib

DOMAIN = "navimower"
SERVICE_PROBE_PRIVATE_API = "probe_private_api"
PROBE_NAMES = ("trail", "reports", "terrain", "weather", "rtk")
PROBE_CHOICES = ("all", *PROBE_NAMES, "terrain_deep")

_REDACTED = "**REDACTED**"
_REDACTED_URL = "**REDACTED_SIGNED_URL**"
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


def _redact_url_values(value: Any) -> Any:
    """Remove signed download URLs while retaining the surrounding response."""
    if isinstance(value, dict):
        return {
            str(key): (
                _REDACTED_URL
                if _normalized_key(key) == "url" and isinstance(item, str) and item
                else _redact_url_values(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_url_values(item) for item in value]
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


def _find_first_key(value: Any, key_name: str) -> Any:
    target = _normalized_key(key_name)
    if isinstance(value, dict):
        for key, item in value.items():
            if _normalized_key(key) == target:
                return item
        for item in value.values():
            found = _find_first_key(item, key_name)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first_key(item, key_name)
            if found is not None:
                return found
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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _byte_inspection(data: bytes) -> dict[str, Any]:
    result: dict[str, Any] = {
        "size_bytes": len(data),
        "sha256": _sha256(data),
        "magic_hex": data[:32].hex(),
    }
    if not data:
        return result

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None:
        result["utf8"] = True
        result["text_length"] = len(text)
        result["text_prefix"] = text[:500]
        stripped = text.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                decoded = json.loads(text)
            except (TypeError, ValueError):
                pass
            else:
                result["json"] = True
                result["json_type"] = type(decoded).__name__
                if isinstance(decoded, dict):
                    result["json_keys"] = list(decoded)[:100]
    else:
        result["utf8"] = False

    decompressed: bytes | None = None
    backend: str | None = None
    if data.startswith(b"\x1f\x8b"):
        try:
            decompressed = gzip.decompress(data)
            backend = "gzip"
        except Exception:  # noqa: BLE001
            pass
    elif data.startswith(b"\x28\xb5\x2f\xfd"):
        try:
            from compression import zstd  # type: ignore[attr-defined]

            decompressed = zstd.decompress(data)
            backend = "compression.zstd"
        except Exception:  # noqa: BLE001
            try:
                import zstandard  # type: ignore[import-not-found]

                decompressed = zstandard.ZstdDecompressor().decompress(data)
                backend = "zstandard"
            except Exception:  # noqa: BLE001
                result["zstd_detected"] = True
                result["zstd_decompression_available"] = False
    elif len(data) >= 2 and data[0] == 0x78:
        try:
            decompressed = zlib.decompress(data)
            backend = "zlib"
        except Exception:  # noqa: BLE001
            pass

    if decompressed is not None:
        result["decompression"] = {
            "backend": backend,
            "output": _byte_inspection(decompressed),
        }
    return result


def _encoded_value_inspection(value: Any) -> dict[str, Any]:
    if isinstance(value, (bytes, bytearray)):
        return {"raw_type": type(value).__name__, "decoded": _byte_inspection(bytes(value))}
    if not isinstance(value, str):
        return {"raw_type": type(value).__name__, "value_present": value is not None}

    raw = value.encode("utf-8")
    result: dict[str, Any] = {
        "raw_type": "string",
        "raw_length": len(value),
        "raw_sha256": _sha256(raw),
        "raw_prefix": value[:160],
    }
    compact = "".join(value.split())
    if compact and len(compact) % 2 == 0 and all(ch in string.hexdigits for ch in compact):
        try:
            decoded = bytes.fromhex(compact)
        except ValueError:
            pass
        else:
            result["hex"] = _byte_inspection(decoded)
    if compact:
        try:
            decoded = base64.b64decode(compact, validate=True)
        except (binascii.Error, ValueError):
            pass
        else:
            if decoded:
                result["base64"] = _byte_inspection(decoded)
    return result


def _download_probe_artifact(url: str, destination: Path) -> dict[str, Any]:
    """Download one vendor-provided signed artifact without publishing its URL."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "Navimower-private-probe/1"})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - explicit vendor URL.
        data = response.read()
        content_type = response.headers.get("Content-Type")
    destination.write_bytes(data)
    parsed = urlsplit(url)
    return {
        "ok": True,
        "local_path": str(destination),
        "url_host": parsed.hostname,
        "content_type": content_type,
        "inspection": _byte_inspection(data),
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


def _terrain_requests(client: Any, sn: str) -> tuple[dict[str, Any], dict[str, Any]]:
    dynamic_map = _call_variants(
        client,
        "/mowerbot/map/queryDynamicsMap",
        _mower_id_variants(sn),
    )
    iot_file = _call_variants(
        client,
        "/mowerbot/vehicle/common/get-iot-file",
        [
            {"vehicle_sn": sn, "type": 2},
            {"sn": sn, "type": 2},
            {"vehicle_sn": sn, "type": "2"},
            {"sn": sn, "type": "2"},
        ],
    )
    return dynamic_map, iot_file


def _probe_terrain(client: Any, sn: str) -> dict[str, Any]:
    dynamic_map, iot_file = _terrain_requests(client, sn)
    return {"dynamic_map": dynamic_map, "iot_file_type_2": iot_file}


def _probe_terrain_deep(
    client: Any,
    sn: str,
    artifact_dir: Path,
    stamp: str,
) -> dict[str, Any]:
    """Inspect H2-style LiDAR/dynamic-map payloads and fetch type-2 artifact."""
    dynamic_map, iot_file = _terrain_requests(client, sn)
    dynamic_data = _selected_data(dynamic_map)
    iot_data = _selected_data(iot_file)
    map_detail = _find_first_key(dynamic_data, "mapDetail")
    signed_url = _find_first_key(iot_data, "url")
    map_version = _find_first_key(dynamic_data, "mapVersion")
    file_version = _find_first_key(iot_data, "version")

    result: dict[str, Any] = {
        "dynamic_map": dynamic_map,
        "iot_file_type_2": _redact_url_values(iot_file),
        "map_version": map_version,
        "file_version": file_version,
        "map_detail_inspection": _encoded_value_inspection(map_detail),
        "artifact_download": {
            "ok": False,
            "reason": "No signed type-2 URL returned",
        },
    }
    if isinstance(signed_url, str) and signed_url.startswith(("https://", "http://")):
        destination = artifact_dir / f"navimower_terrain_type2_{stamp}.bin"
        try:
            result["artifact_download"] = _download_probe_artifact(
                signed_url, destination
            )
        except Exception as err:  # noqa: BLE001 - retain analysis even if URL expires.
            result["artifact_download"] = {
                "ok": False,
                "url_host": urlsplit(signed_url).hostname,
                "error": repr(err),
            }
    return result


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


def _build_probe_document(
    coordinator: Any,
    probe: str,
    artifact_dir: Path,
    stamp: str,
) -> dict[str, Any]:
    if probe not in PROBE_CHOICES:
        raise ValueError(f"Unsupported probe: {probe}")
    client = coordinator.client
    sn = str(coordinator.sn)
    selected = PROBE_NAMES if probe == "all" else (probe,)
    result: dict[str, Any] = {}
    for name in selected:
        if name == "trail":
            result[name] = _probe_trail(client, sn)
        elif name == "reports":
            result[name] = _probe_reports(client, sn)
        elif name == "terrain":
            result[name] = _probe_terrain(client, sn)
        elif name == "terrain_deep":
            result[name] = _probe_terrain_deep(client, sn, artifact_dir, stamp)
        elif name == "weather":
            result[name] = _probe_weather(client, sn)
        elif name == "rtk":
            result[name] = _probe_rtk(client, sn)
    return {
        "format": "navimower-private-api-probe-v2",
        "created_utc": datetime.now(UTC).isoformat(),
        "warning": (
            "EXPLICIT DEVELOPMENT PROBE. Contains exact mower identifiers, request "
            "parameters and potentially exact map/location data. RTK account/password "
            "and signed artifact URLs are redacted. Do not publish this file without review."
        ),
        "source": "navimower.probe_private_api",
        "entry_id": coordinator.entry.entry_id,
        "vehicle_sn": sn,
        "vehicle_type": coordinator.vehicle_type,
        "probe": probe,
        "probes": result,
    }


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, default=repr),
        encoding="utf-8",
    )


def _artifact_paths(document: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            local_path = value.get("local_path")
            if isinstance(local_path, str):
                path = Path(local_path)
                if path.is_file() and path not in paths:
                    paths.append(path)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(document)
    return paths


def _write_bundle(bundle: Path, json_path: Path, artifacts: list[Path]) -> None:
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(json_path, arcname=json_path.name)
        for artifact in artifacts:
            archive.write(artifact, arcname=artifact.name)


async def async_run_private_api_probe(
    hass: Any,
    coordinator: Any,
    probe: str,
) -> dict[str, str | None]:
    """Run one explicit probe batch off the event loop and write its result."""
    now = datetime.now(UTC)
    folder = Path(hass.config.path("navimower_diagnostics", "probes"))
    stamp = now.strftime("%Y%m%d_%H%M%S")
    document = await hass.async_add_executor_job(
        _build_probe_document,
        coordinator,
        probe,
        folder,
        stamp,
    )
    path = folder / f"navimower_probe_{probe}_{stamp}.json"
    latest = folder / f"navimower_probe_{probe}_latest.json"
    await hass.async_add_executor_job(_write_json, path, document)
    await hass.async_add_executor_job(_write_json, latest, document)

    artifacts = await hass.async_add_executor_job(_artifact_paths, document)
    bundle_path: Path | None = None
    if artifacts:
        bundle_path = folder / f"navimower_probe_{probe}_{stamp}.zip"
        await hass.async_add_executor_job(_write_bundle, bundle_path, path, artifacts)
    return {
        "json_path": str(path),
        "bundle_path": str(bundle_path) if bundle_path is not None else None,
    }


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
            result = await async_run_private_api_probe(hass, coordinator, probe)
        except Exception as err:  # noqa: BLE001 - surface field-test failures in HA.
            raise HomeAssistantError(f"Navimower private API probe failed: {err}") from err
        bundle_text = (
            f"\nBundle: `{result['bundle_path']}`\n"
            if result.get("bundle_path")
            else ""
        )
        persistent_notification.async_create(
            hass,
            (
                f"Navimower private API probe `{probe}` completed.\n\n"
                f"File: `{result['json_path']}`\n"
                f"{bundle_text}\n"
                "This development file can contain mower identifiers and exact "
                "map/location data. RTK account/password and signed artifact URLs "
                "are redacted."
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