"""Persistent vendor LiDAR terrain resources for the standalone Map Card."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any
from urllib.request import Request, urlopen
import zipfile

from .api import NavimowAuthError
from .model_capabilities import capability_profile

_LOGGER = logging.getLogger(__name__)

TERRAIN_CACHE_SCHEMA_VERSION = 1
TERRAIN_CHECK_TTL_SECONDS = 15 * 60
TERRAIN_UNAVAILABLE_TTL_SECONDS = 6 * 60 * 60
TERRAIN_DOWNLOAD_TIMEOUT_SECONDS = 30
TERRAIN_MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
TERRAIN_MAX_EXTRACTED_BYTES = 32 * 1024 * 1024
TERRAIN_ENDPOINT = "/mowerbot/vehicle/common/get-iot-file"
TERRAIN_FILE_TYPE = 2


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _find_first_key(value: Any, key_name: str) -> Any:
    target = str(key_name).lower()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() == target:
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


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_version(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_file_version(value: Any) -> str:
    text = _clean_version(value) or "unknown"
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", text).strip("._-")
    return cleaned[:80] or "unknown"


def _is_webp(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _member_with_basename(archive: zipfile.ZipFile, basename: str) -> zipfile.ZipInfo | None:
    basename = Path(str(basename)).name
    if not basename:
        return None
    candidates = [
        info
        for info in archive.infolist()
        if not info.is_dir() and Path(info.filename).name == basename
    ]
    if len(candidates) != 1:
        return None
    return candidates[0]


def _terrain_metadata_from_archive(archive: zipfile.ZipFile) -> tuple[dict[str, Any], zipfile.ZipInfo]:
    candidates: list[tuple[dict[str, Any], zipfile.ZipInfo]] = []
    for info in archive.infolist():
        if info.is_dir() or not info.filename.lower().endswith(".json"):
            continue
        if info.file_size <= 0 or info.file_size > 256 * 1024:
            continue
        try:
            decoded = json.loads(archive.read(info).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(decoded, dict):
            continue
        if decoded.get("terrain_view_image_name") or decoded.get("terrain_adapt_image_name"):
            candidates.append((decoded, info))
    if not candidates:
        raise ValueError("terrain archive metadata JSON not found")
    return candidates[0]


def decode_terrain_archive(
    data: bytes,
    *,
    vendor_file_version: Any,
    map_version: Any = None,
    downloaded_utc: str | None = None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Validate a vendor type-2 ZIP and return normalized metadata + WebP images."""
    if not data.startswith(b"PK"):
        raise ValueError("terrain type-2 resource is not a ZIP archive")
    if len(data) > TERRAIN_MAX_ARCHIVE_BYTES:
        raise ValueError("terrain type-2 archive exceeds size limit")

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        total_uncompressed = sum(max(0, int(info.file_size)) for info in infos)
        if total_uncompressed > TERRAIN_MAX_EXTRACTED_BYTES:
            raise ValueError("terrain type-2 archive expands beyond size limit")

        source, _source_info = _terrain_metadata_from_archive(archive)
        min_x = _as_float(source.get("minX"))
        max_x = _as_float(source.get("maxX"))
        min_y = _as_float(source.get("minY"))
        max_y = _as_float(source.get("maxY"))
        if None in (min_x, max_x, min_y, max_y):
            raise ValueError("terrain metadata is missing XY extent")
        assert min_x is not None and max_x is not None
        assert min_y is not None and max_y is not None
        if not (min_x < max_x and min_y < max_y):
            raise ValueError("terrain metadata has invalid XY extent")

        images: dict[str, bytes] = {}
        source_names = {
            "terrain": source.get("terrain_view_image_name"),
            "elevation": source.get("terrain_adapt_image_name"),
        }
        image_meta: dict[str, dict[str, Any]] = {}
        for kind, source_name in source_names.items():
            if not source_name:
                continue
            info = _member_with_basename(archive, str(source_name))
            if info is None:
                continue
            if info.file_size <= 0 or info.file_size > TERRAIN_MAX_EXTRACTED_BYTES:
                continue
            body = archive.read(info)
            if not _is_webp(body):
                continue
            images[kind] = body
            image_meta[kind] = {
                "source_name": Path(str(source_name)).name,
                "size_bytes": len(body),
            }

        if not images:
            raise ValueError("terrain archive contains no usable WebP resource")

        pixel_per_meter = _as_float(source.get("pixel_per_meter"))
        grid_map_size = _as_float(source.get("grid_map_size"))
        adapt_max_height = _as_float(source.get("terrain_adapt_max_height"))
        min_z = _as_float(source.get("minZ"))
        max_z = _as_float(source.get("maxZ"))

        manifest = {
            "schema_version": TERRAIN_CACHE_SCHEMA_VERSION,
            "vendor_file_version": _clean_version(vendor_file_version),
            "map_version": _clean_version(map_version),
            "downloaded_utc": downloaded_utc or _utc_now_iso(),
            "reference_frame": "mower_local_xy",
            "extent": {
                "min_x": min_x,
                "max_x": max_x,
                "min_y": min_y,
                "max_y": max_y,
            },
            "pixel_per_meter": pixel_per_meter,
            "grid_map_size_m": grid_map_size,
            "adapt_max_height_m": adapt_max_height,
            "z_range_m": {
                "min": min_z,
                "max": max_z,
            },
            "source_width": source.get("width"),
            "source_height": source.get("height"),
            "images": image_meta,
        }
        return manifest, images


def _download_signed_resource(url: str) -> bytes:
    """Download one short-lived vendor URL without ever persisting the URL."""
    request = Request(url, headers={"User-Agent": "Navimower-terrain-cache/1"})
    with urlopen(request, timeout=TERRAIN_DOWNLOAD_TIMEOUT_SECONDS) as response:  # noqa: S310
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared_size = int(content_length)
            except (TypeError, ValueError):
                declared_size = None
            if declared_size is not None and declared_size > TERRAIN_MAX_ARCHIVE_BYTES:
                raise ValueError("terrain type-2 archive exceeds size limit")
        data = response.read(TERRAIN_MAX_ARCHIVE_BYTES + 1)
    if len(data) > TERRAIN_MAX_ARCHIVE_BYTES:
        raise ValueError("terrain type-2 archive exceeds size limit")
    return data


def _safe_error_label(err: Exception) -> str:
    """Return diagnostics-safe error text that cannot leak a signed URL."""
    status = getattr(err, "code", None)
    if isinstance(status, int):
        return f"{type(err).__name__}: http_{status}"
    return type(err).__name__


class TerrainOverlayManager:
    """Version-check, persist and expose the vendor LiDAR terrain package."""

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self.entry_id = str(coordinator.entry.entry_id)
        self.cache_dir = Path(
            self.hass.config.path(".storage", "navimower_terrain", self.entry_id)
        )
        self.manifest_path = self.cache_dir / "manifest.json"
        self._manifest: dict[str, Any] | None = None
        self._last_attempt_mono: float | None = None
        self._last_check_utc: str | None = None
        self._last_success_utc: str | None = None
        self._last_error: str | None = None
        self._task: asyncio.Task | None = None

    @property
    def model_hint(self) -> bool:
        """Return the model-profile LiDAR hint used only for polling cadence."""
        data = self.coordinator.data or {}
        model = data.get("model") or self.coordinator.entry.data.get("model")
        vehicle_type = data.get("vehicle_type")
        if vehicle_type is None:
            vehicle_type = getattr(self.coordinator, "vehicle_type", None)
        return bool(
            capability_profile(model, vehicle_type).lidar_terrain_overlay
        )

    @property
    def supported(self) -> bool:
        """Return whether a valid vendor type-2 terrain resource is proven."""
        manifest = self._manifest
        return bool(
            isinstance(manifest, dict)
            and isinstance(manifest.get("images"), dict)
            and manifest.get("images")
        )

    async def async_load(self) -> None:
        await self.hass.async_add_executor_job(self._load_cache_blocking)

    def start(self) -> None:
        """Start the independent slow terrain discovery/refresh loop."""
        if self._task is not None and not self._task.done():
            return
        self._task = self.hass.async_create_background_task(
            self._async_loop(),
            f"Navimower terrain overlay {self.entry_id}",
        )

    async def async_stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def async_refresh(self) -> None:
        await self.hass.async_add_executor_job(
            self.refresh_blocking,
            self.coordinator.data or {},
        )

    async def _async_loop(self) -> None:
        while True:
            try:
                await self.async_refresh()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - optional terrain must never stop runtime.
                self._last_error = _safe_error_label(err)
                _LOGGER.debug(
                    "Terrain overlay background refresh failed; retrying later (%s)",
                    self._last_error,
                )
            delay = (
                TERRAIN_CHECK_TTL_SECONDS
                if self._manifest is not None or self.model_hint
                else TERRAIN_UNAVAILABLE_TTL_SECONDS
            )
            await asyncio.sleep(delay)

    def _load_cache_blocking(self) -> None:
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            self._manifest = None
            return
        if not isinstance(raw, dict) or raw.get("schema_version") != TERRAIN_CACHE_SCHEMA_VERSION:
            self._manifest = None
            return
        images = raw.get("images")
        if not isinstance(images, dict) or not images:
            self._manifest = None
            return
        for value in images.values():
            if not isinstance(value, dict):
                self._manifest = None
                return
            filename = value.get("cache_name")
            if not filename or not (self.cache_dir / str(filename)).is_file():
                self._manifest = None
                return
        self._manifest = raw

    def _write_cache_blocking(
        self,
        manifest: dict[str, Any],
        images: dict[str, bytes],
    ) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        version = _safe_file_version(manifest.get("vendor_file_version"))
        final_manifest = json.loads(json.dumps(manifest))
        final_images: dict[str, dict[str, Any]] = {}

        for kind, body in images.items():
            filename = f"{kind}-{version}.webp"
            final_path = self.cache_dir / filename
            temp_path = self.cache_dir / f".{filename}.tmp"
            temp_path.write_bytes(body)
            os.replace(temp_path, final_path)
            meta = dict((manifest.get("images") or {}).get(kind) or {})
            meta["cache_name"] = filename
            meta["size_bytes"] = len(body)
            final_images[kind] = meta

        final_manifest["images"] = final_images
        temp_manifest = self.cache_dir / ".manifest.json.tmp"
        temp_manifest.write_text(
            json.dumps(final_manifest, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temp_manifest, self.manifest_path)
        self._manifest = final_manifest

        referenced = {str(item.get("cache_name")) for item in final_images.values()}
        for path in self.cache_dir.glob("*.webp"):
            if path.name not in referenced:
                try:
                    path.unlink()
                except OSError:
                    pass

    def _cloud_metadata(self) -> tuple[str, str]:
        """Return (vendor file version, signed URL) using bounded request variants."""
        last_error: Exception | None = None
        requests = (
            {"vehicle_sn": self.coordinator.sn, "type": TERRAIN_FILE_TYPE},
            {"sn": self.coordinator.sn, "type": TERRAIN_FILE_TYPE},
            {"vehicle_sn": self.coordinator.sn, "type": str(TERRAIN_FILE_TYPE)},
            {"sn": self.coordinator.sn, "type": str(TERRAIN_FILE_TYPE)},
        )
        for request in requests:
            try:
                result = self.coordinator.client.call(TERRAIN_ENDPOINT, request)
            except NavimowAuthError:
                raise
            except Exception as err:  # noqa: BLE001 - unsupported models reject this endpoint.
                last_error = err
                continue
            version = _clean_version(_find_first_key(result, "version"))
            signed_url = _find_first_key(result, "url")
            if version and isinstance(signed_url, str) and signed_url.startswith(("https://", "http://")):
                return version, signed_url
            last_error = ValueError("terrain file metadata missing version or URL")
        if last_error is not None:
            raise last_error
        raise RuntimeError("terrain file metadata unavailable")

    def refresh_blocking(self, snapshot: dict[str, Any] | None = None) -> None:
        """Discover/check type-2 metadata and download only on version change."""
        snapshot = snapshot or {}
        map_data = snapshot.get("map") if isinstance(snapshot.get("map"), dict) else {}
        map_version = _clean_version(
            map_data.get("map_version") or snapshot.get("map_version")
        )
        now = time.monotonic()
        cached = self._manifest is not None
        ttl = (
            TERRAIN_CHECK_TTL_SECONDS
            if cached or self.model_hint
            else TERRAIN_UNAVAILABLE_TTL_SECONDS
        )
        due = (
            self._last_attempt_mono is None
            or now - self._last_attempt_mono >= ttl
        )
        if not due:
            return

        self._last_attempt_mono = now
        self._last_check_utc = _utc_now_iso()
        try:
            vendor_version, signed_url = self._cloud_metadata()
            cached_version = _clean_version((self._manifest or {}).get("vendor_file_version"))
            if cached_version == vendor_version and self._manifest is not None:
                self._last_success_utc = self._last_check_utc
                self._last_error = None
                return

            archive = _download_signed_resource(signed_url)
            manifest, images = decode_terrain_archive(
                archive,
                vendor_file_version=vendor_version,
                map_version=map_version,
                downloaded_utc=self._last_check_utc,
            )
            self._write_cache_blocking(manifest, images)
            self._last_success_utc = self._last_check_utc
            self._last_error = None
        except NavimowAuthError:
            raise
        except Exception as err:  # noqa: BLE001 - cached terrain must survive cloud failures.
            self._last_error = _safe_error_label(err)
            _LOGGER.debug(
                "Terrain overlay refresh failed; retaining any cached resource (%s)",
                self._last_error,
            )

    def frontend_metadata(self) -> dict[str, Any]:
        manifest = self._manifest
        if not isinstance(manifest, dict):
            return {
                "supported": self.supported,
                "available": False,
                "reference_frame": "mower_local_xy",
                "version": None,
                "terrain": {"available": False},
                "elevation": {"available": False},
            }

        images = manifest.get("images") if isinstance(manifest.get("images"), dict) else {}
        version = _clean_version(manifest.get("vendor_file_version"))
        base_path = f"/api/navimower/terrain/{self.entry_id}"
        return {
            "supported": self.supported,
            "available": bool(images),
            "reference_frame": manifest.get("reference_frame") or "mower_local_xy",
            "version": version,
            "map_version": manifest.get("map_version"),
            "extent": manifest.get("extent"),
            "pixel_per_meter": manifest.get("pixel_per_meter"),
            "grid_map_size_m": manifest.get("grid_map_size_m"),
            "adapt_max_height_m": manifest.get("adapt_max_height_m"),
            "terrain": {
                "available": "terrain" in images,
                "api_path": f"{base_path}/terrain" if "terrain" in images else None,
            },
            "elevation": {
                "available": "elevation" in images,
                "api_path": f"{base_path}/elevation" if "elevation" in images else None,
            },
        }

    def image_resource(self, kind: str) -> tuple[Path, str] | None:
        if kind not in {"terrain", "elevation"}:
            return None
        manifest = self._manifest
        if not isinstance(manifest, dict):
            return None
        images = manifest.get("images")
        if not isinstance(images, dict):
            return None
        item = images.get(kind)
        if not isinstance(item, dict):
            return None
        filename = item.get("cache_name")
        if not filename:
            return None
        path = self.cache_dir / str(filename)
        if not path.is_file():
            return None
        return path, _clean_version(manifest.get("vendor_file_version")) or "unknown"

    def diagnostics(self) -> dict[str, Any]:
        manifest = self._manifest if isinstance(self._manifest, dict) else {}
        images = manifest.get("images") if isinstance(manifest.get("images"), dict) else {}
        return {
            "supported": self.supported,
            "support_source": "vendor_type2_resource" if self.supported else None,
            "model_hint": self.model_hint,
            "available": bool(images),
            "cached": bool(images),
            "vendor_file_version": manifest.get("vendor_file_version"),
            "map_version": manifest.get("map_version"),
            "last_check_utc": self._last_check_utc,
            "last_success_utc": self._last_success_utc,
            "last_error": self._last_error,
            "terrain_size_bytes": (images.get("terrain") or {}).get("size_bytes"),
            "elevation_size_bytes": (images.get("elevation") or {}).get("size_bytes"),
        }

    @staticmethod
    async def async_remove_all(hass: Any, entry_id: str) -> None:
        path = Path(hass.config.path(".storage", "navimower_terrain", str(entry_id)))
        await hass.async_add_executor_job(shutil.rmtree, path, True)
