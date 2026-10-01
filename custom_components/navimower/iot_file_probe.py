"""Explicit prerelease probe for Navimow get-iot-file resources.

This is a bounded maintainer-only research surface. It can call exactly one
known read-only vendor endpoint and only for a small integer file-type set.
It never accepts an arbitrary path or payload. Signed download URLs are used
only in memory and are redacted from persisted JSON.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

from .discovery import structure_summary

IOT_FILE_ENDPOINT = "/mowerbot/vehicle/common/get-iot-file"
IOT_FILE_TYPE_MIN = 0
IOT_FILE_TYPE_MAX = 15
IOT_FILE_MAX_TYPES_PER_RUN = 8
DEFAULT_IOT_FILE_TYPES = (0, 1, 2, 3, 4, 5)
IOT_FILE_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
IOT_FILE_DOWNLOAD_TIMEOUT_SECONDS = 30

_REDACTED_SIGNED_URL = "**REDACTED_SIGNED_URL**"
_URL_KEYS = {
    "url",
    "downloadurl",
    "download_url",
    "fileurl",
    "file_url",
    "signedurl",
    "signed_url",
}
_VERSION_KEYS = {"version", "resourceversion", "resource_version", "fileversion", "file_version"}


def _normalized_key(value: Any) -> str:
    return "".join(ch for ch in str(value).strip().lower() if ch.isalnum())


def _find_first(value: Any, keys: set[str]) -> Any:
    targets = {_normalized_key(key) for key in keys}
    if isinstance(value, dict):
        for key, item in value.items():
            if _normalized_key(key) in targets and item not in (None, ""):
                return item
        for item in value.values():
            found = _find_first(item, keys)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first(item, keys)
            if found not in (None, ""):
                return found
    return None


def _redact_signed_urls(value: Any) -> Any:
    """Persist response structure/metadata without storing short-lived URLs."""
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        return _REDACTED_SIGNED_URL
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if (
                _normalized_key(key) in {_normalized_key(name) for name in _URL_KEYS}
                and isinstance(item, str)
                and item.startswith(("https://", "http://"))
            ):
                out[str(key)] = _REDACTED_SIGNED_URL
            else:
                out[str(key)] = _redact_signed_urls(item)
        return out
    if isinstance(value, list):
        return [_redact_signed_urls(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_signed_urls(item) for item in value]
    return deepcopy(value)


def _normalize_types(values: Any) -> list[int]:
    """Return a bounded, de-duplicated list of file types."""
    raw = list(values or DEFAULT_IOT_FILE_TYPES)
    result: list[int] = []
    for value in raw:
        try:
            file_type = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid IoT file type: {value!r}") from None
        if not IOT_FILE_TYPE_MIN <= file_type <= IOT_FILE_TYPE_MAX:
            raise ValueError(
                f"IoT file type {file_type} is outside the bounded "
                f"{IOT_FILE_TYPE_MIN}..{IOT_FILE_TYPE_MAX} research range"
            )
        if file_type not in result:
            result.append(file_type)
    if not result:
        raise ValueError("At least one IoT file type is required")
    if len(result) > IOT_FILE_MAX_TYPES_PER_RUN:
        raise ValueError(
            f"At most {IOT_FILE_MAX_TYPES_PER_RUN} IoT file types may be probed per run"
        )
    return result


def _safe_error(err: Exception) -> dict[str, Any]:
    message = re.sub(r"https?://\S+", "<redacted-url>", str(err))
    return {
        "type": type(err).__name__,
        "message": message[:500],
    }


def _call_type(client: Any, sn: str, file_type: int) -> dict[str, Any]:
    """Try the two observed mower-id parameter names and retain evidence."""
    attempts: list[dict[str, Any]] = []
    selected: int | None = None
    selected_data: Any = None
    requests = (
        {"vehicle_sn": sn, "type": file_type},
        {"sn": sn, "type": file_type},
    )
    for index, request in enumerate(requests):
        try:
            data = client.call(IOT_FILE_ENDPOINT, request)
        except Exception as err:  # noqa: BLE001 - research keeps per-variant failure.
            attempts.append(
                {
                    "request_shape": sorted(request),
                    "ok": False,
                    "error": _safe_error(err),
                }
            )
            continue
        attempts.append(
            {
                "request_shape": sorted(request),
                "ok": True,
                "response_type": type(data).__name__,
            }
        )
        selected = index
        selected_data = data
        break

    result: dict[str, Any] = {
        "file_type": file_type,
        "ok": selected is not None,
        "selected_attempt": selected,
        "attempts": attempts,
    }
    if selected is None:
        return result

    signed_url = _find_first(selected_data, _URL_KEYS)
    version = _find_first(selected_data, _VERSION_KEYS)
    result.update(
        {
            "version": str(version) if version not in (None, "") else None,
            "signed_url_present": bool(
                isinstance(signed_url, str)
                and signed_url.startswith(("https://", "http://"))
            ),
            "signed_url_host": (
                urlsplit(signed_url).hostname
                if isinstance(signed_url, str)
                and signed_url.startswith(("https://", "http://"))
                else None
            ),
            "structure": structure_summary(selected_data),
            "response": _redact_signed_urls(selected_data),
        }
    )
    if isinstance(signed_url, str) and signed_url.startswith(("https://", "http://")):
        result["_signed_url_runtime"] = signed_url
    return result


def _artifact_kind(data: bytes, content_type: str | None) -> str:
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"\x1f\x8b"):
        return "gzip"
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        return "zstd"
    stripped = data.lstrip()
    if stripped.startswith((b"{", b"[")):
        return "json_or_text"
    if content_type:
        lowered = content_type.lower()
        if "json" in lowered:
            return "json_or_text"
        if "zip" in lowered:
            return "zip"
    return "unknown"


def _inspect_artifact(data: bytes, content_type: str | None) -> dict[str, Any]:
    kind = _artifact_kind(data, content_type)
    result: dict[str, Any] = {
        "kind": kind,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "magic_hex": data[:32].hex(),
        "content_type": content_type,
    }
    if kind == "zip":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = [
                    {
                        "name": info.filename.replace("\\", "/"),
                        "size_bytes": int(info.file_size),
                        "compressed_size_bytes": int(info.compress_size),
                    }
                    for info in archive.infolist()
                    if not info.is_dir()
                ]
        except zipfile.BadZipFile:
            result["zip_valid"] = False
        else:
            result["zip_valid"] = True
            result["entry_count"] = len(entries)
            result["entries"] = entries[:200]
            if len(entries) > 200:
                result["entries_truncated"] = len(entries) - 200
    elif kind == "json_or_text":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            result["text_prefix"] = text[:1000]
            try:
                decoded = json.loads(text)
            except (TypeError, ValueError):
                pass
            else:
                result["json_structure"] = structure_summary(decoded)
    return result


def _download_artifact(
    signed_url: str,
    destination: Path,
) -> dict[str, Any]:
    """Download one vendor-signed resource under a hard byte limit."""
    request = Request(signed_url, headers={"User-Agent": "Navimower-iot-file-probe/1"})
    with urlopen(request, timeout=IOT_FILE_DOWNLOAD_TIMEOUT_SECONDS) as response:  # noqa: S310
        content_type = response.headers.get("Content-Type")
        declared = response.headers.get("Content-Length")
        if declared:
            try:
                declared_size = int(declared)
            except (TypeError, ValueError):
                declared_size = None
            if declared_size is not None and declared_size > IOT_FILE_MAX_ARTIFACT_BYTES:
                raise ValueError("IoT file artifact exceeds the 16 MiB probe limit")
        data = response.read(IOT_FILE_MAX_ARTIFACT_BYTES + 1)
    if len(data) > IOT_FILE_MAX_ARTIFACT_BYTES:
        raise ValueError("IoT file artifact exceeds the 16 MiB probe limit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return {
        "ok": True,
        "local_path": str(destination),
        "url_host": urlsplit(signed_url).hostname,
        "inspection": _inspect_artifact(data, content_type),
    }


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, default=repr),
        encoding="utf-8",
    )


def _write_bundle(bundle: Path, json_path: Path, artifacts: list[Path]) -> None:
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(json_path, arcname=json_path.name)
        for artifact in artifacts:
            if artifact.is_file():
                archive.write(artifact, arcname=artifact.name)


def _run_probe_blocking(
    coordinator: Any,
    file_types: list[int],
    download_artifacts: bool,
    folder: Path,
    stamp: str,
) -> tuple[dict[str, Any], list[Path]]:
    rows: list[dict[str, Any]] = []
    artifacts: list[Path] = []
    for file_type in file_types:
        row = _call_type(coordinator.client, str(coordinator.sn), file_type)
        signed_url = row.pop("_signed_url_runtime", None)
        if download_artifacts and isinstance(signed_url, str):
            destination = folder / f"navimower_iot_file_type{file_type}_{stamp}.bin"
            try:
                row["artifact_download"] = _download_artifact(
                    signed_url,
                    destination,
                )
            except Exception as err:  # noqa: BLE001 - retain metadata on download failure.
                row["artifact_download"] = {
                    "ok": False,
                    "url_host": urlsplit(signed_url).hostname,
                    "error": _safe_error(err),
                }
            else:
                artifacts.append(destination)
        else:
            row["artifact_download"] = {
                "ok": False,
                "skipped": True,
                "reason": (
                    "download_artifacts disabled"
                    if not download_artifacts
                    else "No signed URL returned"
                ),
            }
        rows.append(row)

    document = {
        "format": "navimower-iot-file-probe-v1",
        "created_utc": datetime.now(UTC).isoformat(),
        "source": "navimower.probe_iot_file",
        "warning": (
            "PRERELEASE MAINTAINER RESEARCH. The fixed endpoint is read-only, "
            "but this file can contain mower identifiers and vendor metadata. "
            "Signed download URLs are never persisted. Review before sharing."
        ),
        "entry_id": coordinator.entry.entry_id,
        "vehicle_sn": str(coordinator.sn),
        "vehicle_type": coordinator.vehicle_type,
        "endpoint": IOT_FILE_ENDPOINT,
        "requested_file_types": file_types,
        "download_artifacts": bool(download_artifacts),
        "results": rows,
    }
    return document, artifacts


async def async_probe_iot_file(
    hass: Any,
    coordinator: Any,
    file_types: Any,
    *,
    download_artifacts: bool = False,
) -> dict[str, Any]:
    """Run the bounded get-iot-file probe and persist its development result."""
    normalized = _normalize_types(file_types)
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    folder = Path(hass.config.path("navimower_diagnostics", "probes"))
    document, artifacts = await hass.async_add_executor_job(
        _run_probe_blocking,
        coordinator,
        normalized,
        bool(download_artifacts),
        folder,
        stamp,
    )

    json_path = folder / f"navimower_iot_file_probe_{stamp}.json"
    latest_path = folder / "navimower_iot_file_probe_latest.json"
    await hass.async_add_executor_job(_write_json, json_path, document)
    await hass.async_add_executor_job(_write_json, latest_path, document)

    bundle_path: Path | None = None
    if artifacts:
        bundle_path = folder / f"navimower_iot_file_probe_{stamp}.zip"
        await hass.async_add_executor_job(
            _write_bundle,
            bundle_path,
            json_path,
            artifacts,
        )

    return {
        "json_path": str(json_path),
        "latest_path": str(latest_path),
        "bundle_path": str(bundle_path) if bundle_path is not None else None,
        "file_types": normalized,
        "successful_types": [
            int(row["file_type"])
            for row in document["results"]
            if row.get("ok") is True
        ],
        "artifact_types": [
            int(row["file_type"])
            for row in document["results"]
            if isinstance(row.get("artifact_download"), dict)
            and row["artifact_download"].get("ok") is True
        ],
    }
