"""Protect a validated map transform from stationary cloud-GPS drift.

The learned transform is tied to the decoded map revision.  Once that transform
has passed the multi-point fit, repeated GPS validation mismatches are useful
diagnostics but are not sufficient evidence that the map frame changed.  The
map revision is the authoritative relearn trigger.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import coordinator_semantics as _coordinator_semantics
from . import georeference as _georeference

_LEARNED_VALIDATION_LIMIT_M = 3.0


def _validated_fit(calibration: Any, revision: str) -> dict[str, Any] | None:
    if not isinstance(calibration, dict) or calibration.get("map_revision") != revision:
        return None
    fit = calibration.get("fit")
    if not isinstance(fit, dict) or not _georeference.georeference_is_valid(fit):
        return None
    return fit


def install_georeference_semantics() -> None:
    """Freeze a validated fit until the decoded map revision actually changes."""
    if getattr(_georeference, "_stable_revision_fit_installed", False):
        return

    original_update = _georeference.update_georeference

    def update_georeference(map_geometry: Any, location: Any) -> dict[str, Any] | None:
        if not isinstance(map_geometry, dict):
            return original_update(map_geometry, location)

        revision = str(map_geometry.get("revision") or "")
        calibration_before = map_geometry.get("_georeference_calibration")
        frozen_fit = _validated_fit(calibration_before, revision)
        frozen_state = (
            deepcopy(calibration_before) if frozen_fit is not None else None
        )
        frozen_fit_copy = deepcopy(frozen_fit) if frozen_fit is not None else None

        result = original_update(map_geometry, location)
        if frozen_state is None or frozen_fit_copy is None:
            return result

        # The old learner reset after three consecutive >3 m cloud-GPS
        # mismatches.  A stationary mower can produce exactly that while the
        # already fitted local map remains perfectly correct.  Preserve the fit
        # and samples; only update passive validation diagnostics.
        validated = _georeference.validate_georeference(
            frozen_fit_copy,
            location,
            limit_m=_LEARNED_VALIDATION_LIMIT_M,
        ) or frozen_fit_copy
        validation = dict((validated.get("validation") or {}))
        previous_mismatches = int(frozen_state.get("mismatch_count") or 0)
        if validation.get("valid") is False:
            frozen_state["mismatch_count"] = previous_mismatches + 1
        elif validation.get("valid") is True:
            frozen_state["mismatch_count"] = 0
        else:
            frozen_state["mismatch_count"] = previous_mismatches
        frozen_state["last_validation"] = validation
        frozen_state["fit"] = frozen_fit_copy
        map_geometry["_georeference_calibration"] = frozen_state

        active = dict(validated)
        active["schema_version"] = 2
        active["source"] = "cloud_location_fit"
        active["status"] = "validated"
        active["map_revision"] = revision
        active["calibration"] = _georeference._calibration_summary(  # noqa: SLF001
            frozen_state
        )

        vendor = map_geometry.get("_vendor_georeference")
        _, vendor_hint = _georeference._vendor_hint(  # noqa: SLF001
            vendor if isinstance(vendor, dict) else None,
            location,
            active,
        )
        if vendor_hint is not None:
            active["vendor_hint"] = vendor_hint

        map_geometry["georeference"] = active
        return active

    # Coordinator semantics imported the helper directly, so update both the
    # source module and that bound runtime reference.
    _georeference.update_georeference = update_georeference
    _coordinator_semantics.update_georeference = update_georeference
    _georeference._stable_revision_fit_installed = True


__all__ = ["install_georeference_semantics"]
