"""Attach coordinate-frame comparison diagnostics to georeference metadata."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .coordinator_semantics import NavimowCoordinator
from .georeference_reference_diagnostics import reference_candidate_diagnostics
from .georeference_tools import local_frame_diagnostics

_INSTALLED = False
_ORIGINAL_PARSE = NavimowCoordinator._parse


def _parse(self: NavimowCoordinator, raw: dict[str, Any]) -> dict[str, Any]:
    snapshot = _ORIGINAL_PARSE(self, raw)
    georeference = snapshot.get("georeference")
    if isinstance(georeference, dict):
        decorated = deepcopy(georeference)
        decorated["local_frame_check"] = local_frame_diagnostics(self, snapshot)
        geometry = getattr(self, "_map_geometry", None)
        snapshot_raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
        location = snapshot_raw.get("location") if isinstance(snapshot_raw.get("location"), dict) else None
        decorated["reference_candidates"] = reference_candidate_diagnostics(
            geometry,
            georeference,
            location,
            docked=bool(snapshot.get("docked")),
        )
        snapshot["georeference"] = decorated
        map_data = snapshot.get("map")
        if isinstance(map_data, dict):
            map_data = dict(map_data)
            map_data["georeference"] = decorated
            snapshot["map"] = map_data
    return snapshot


def install_georeference_diagnostics_semantics() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    NavimowCoordinator._parse = _parse
    _INSTALLED = True


__all__ = ["install_georeference_diagnostics_semantics"]
