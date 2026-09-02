"""Expose provider georeference frames through existing frontend/site APIs."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from .georeference_frames import (
    FRAME_ACTIVE,
    FRAME_REGIONAL_CARTOGRAPHIC,
    FRAME_WEB_WGS84,
    build_georeference_frames,
    georeference_frame_diagnostics,
    site_underlay_origins,
)

_INSTALLED = False
_ORIGINAL_FRONTEND_METADATA: Callable[[Any], dict[str, Any]] | None = None
_ORIGINAL_SITE_PAYLOAD: Callable[..., dict[str, Any]] | None = None
_ORIGINAL_DIAGNOSTICS: Callable[..., Any] | None = None

_PROVIDER_FRAMES = {
    "openstreetmap": FRAME_WEB_WGS84,
    "google_satellite": FRAME_WEB_WGS84,
    "estonia_orthophoto": FRAME_REGIONAL_CARTOGRAPHIC,
    "estonia_hybrid": FRAME_REGIONAL_CARTOGRAPHIC,
}


def _frontend_metadata(coordinator: Any) -> dict[str, Any]:
    if _ORIGINAL_FRONTEND_METADATA is None:
        return {}
    result = deepcopy(_ORIGINAL_FRONTEND_METADATA(coordinator))
    frames = build_georeference_frames(coordinator)
    result["georeference_frames"] = frames
    underlays = result.get("map_underlays")
    if not isinstance(underlays, dict):
        underlays = {}
        result["map_underlays"] = underlays
    underlays.setdefault("openstreetmap", {"available": True})
    for provider, frame_name in _PROVIDER_FRAMES.items():
        provider_metadata = underlays.setdefault(provider, {})
        if not isinstance(provider_metadata, dict):
            provider_metadata = {}
            underlays[provider] = provider_metadata
        provider_metadata["reference_frame"] = frame_name
        provider_metadata["reference_frame_available"] = (
            (frames.get(frame_name) or {}).get("available") is True
        )
        provider_metadata["reference_frame_fallback"] = FRAME_ACTIVE
    return result


def _site_payload(
    root_entry_id: str,
    coordinators: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    if _ORIGINAL_SITE_PAYLOAD is None:
        return {}
    result = _ORIGINAL_SITE_PAYLOAD(root_entry_id, coordinators, **kwargs)
    root = coordinators.get(root_entry_id)
    if root is not None:
        result["underlay_origins"] = site_underlay_origins(result.get("origin"), root)
        result["underlay_reference_frames"] = dict(_PROVIDER_FRAMES)
    return result


async def _diagnostics(hass: Any, entry: Any) -> dict[str, Any]:
    if _ORIGINAL_DIAGNOSTICS is None:
        return {}
    result = await _ORIGINAL_DIAGNOSTICS(hass, entry)
    coordinator = ((hass.data.get("navimower") or {}).get(entry.entry_id))
    if coordinator is not None and isinstance(result, dict):
        result["georeference_frames"] = georeference_frame_diagnostics(coordinator)
        notes = result.get("notes")
        if isinstance(notes, list):
            notes.append(
                "Provider georeference-frame diagnostics expose only frame availability, source, quality and relative metre offsets; frame GPS references are omitted."
            )
    return result


def install_georeference_frames_semantics() -> None:
    """Install provider-frame API decoration after georeference semantics."""
    global _INSTALLED, _ORIGINAL_FRONTEND_METADATA, _ORIGINAL_SITE_PAYLOAD, _ORIGINAL_DIAGNOSTICS
    if _INSTALLED:
        return

    from . import diagnostics as _diagnostics_module
    from . import map_api as _map_api

    _ORIGINAL_FRONTEND_METADATA = _map_api._frontend_metadata  # noqa: SLF001
    _ORIGINAL_SITE_PAYLOAD = _map_api.build_site_payload
    _ORIGINAL_DIAGNOSTICS = _diagnostics_module.async_get_config_entry_diagnostics

    _map_api._frontend_metadata = _frontend_metadata  # noqa: SLF001
    _map_api.build_site_payload = _site_payload
    _diagnostics_module.async_get_config_entry_diagnostics = _diagnostics
    _INSTALLED = True


__all__ = ["install_georeference_frames_semantics"]
