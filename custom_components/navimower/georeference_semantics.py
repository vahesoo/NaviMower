"""Keep map georeference stable while refining it from wider movement samples.

The first usable cloud-location fit is intentionally available early so map
consumers do not have to wait for a whole mowing cycle.  It is not, however,
considered the final orientation.  For the same map revision we keep collecting
well-separated local-X/Y + WGS84 pairs and improve the transform in three
stages:

* provisional: the existing first fit at >=5 samples;
* refined: re-fit at 8, 9 and 10 samples;
* high confidence: re-fit from >=12 samples onward, up to the learner's sample
  cap.

Sample separation is still enforced by the base learner, so stationary GPS
wander cannot manufacture calibration points.  A validated transform is never
thrown away merely because later stationary cloud GPS validation drifts; map
revision remains the authoritative reset trigger.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import georeference as _georeference

_LEARNED_VALIDATION_LIMIT_M = 3.0
_REFINED_SAMPLE_COUNTS = frozenset({8, 9, 10})
_HIGH_CONFIDENCE_MIN_SAMPLES = 12
_ORIGINAL_UPDATE_GEOREFERENCE = _georeference.update_georeference


def _validated_fit(calibration: Any, revision: str) -> dict[str, Any] | None:
    if not isinstance(calibration, dict) or calibration.get("map_revision") != revision:
        return None
    fit = calibration.get("fit")
    if not isinstance(fit, dict) or not _georeference.georeference_is_valid(fit):
        return None
    return fit


def _refinement_stage(sample_count: int) -> str:
    if sample_count >= _HIGH_CONFIDENCE_MIN_SAMPLES:
        return "high_confidence"
    if sample_count >= min(_REFINED_SAMPLE_COUNTS):
        return "refined"
    if sample_count >= 5:
        return "provisional"
    return "learning"


def _should_refit(sample_count: int) -> bool:
    return sample_count in _REFINED_SAMPLE_COUNTS or sample_count >= _HIGH_CONFIDENCE_MIN_SAMPLES


def _append_refinement_sample(state: dict[str, Any], location: Any) -> bool:
    sample = _georeference._current_location_sample(location)  # noqa: SLF001
    if sample is None:
        return False
    samples = [
        dict(item) for item in state.get("samples") or [] if isinstance(item, dict)
    ]
    state["samples"] = samples
    return _georeference._append_sample(samples, sample)  # noqa: SLF001


def _refine_fit_if_due(
    state: dict[str, Any], current_fit: dict[str, Any]
) -> dict[str, Any]:
    samples = state.get("samples") or []
    sample_count = len(samples)
    state["refinement_stage"] = _refinement_stage(sample_count)
    state["refinement_sample_count"] = sample_count

    if not _should_refit(sample_count):
        return current_fit

    candidate = _georeference._fit_samples(samples)  # noqa: SLF001
    if not isinstance(candidate, dict) or candidate.get("status") != "validated":
        state["last_refinement_result"] = "rejected"
        return current_fit

    refined = {
        key: deepcopy(value)
        for key, value in candidate.items()
        if key != "validation"
    }
    refined["refinement_stage"] = _refinement_stage(sample_count)
    state["fit"] = refined
    state["last_refinement_result"] = "accepted"
    state["last_refinement_sample_count"] = sample_count
    return refined


def stable_update_georeference(
    map_geometry: Any, location: Any
) -> dict[str, Any] | None:
    """Update georeference with staged refinement and revision-stable safety."""
    if not isinstance(map_geometry, dict):
        return _ORIGINAL_UPDATE_GEOREFERENCE(map_geometry, location)

    revision = str(map_geometry.get("revision") or "")
    calibration_before = map_geometry.get("_georeference_calibration")
    frozen_fit = _validated_fit(calibration_before, revision)
    frozen_state = deepcopy(calibration_before) if frozen_fit is not None else None
    frozen_fit_copy = deepcopy(frozen_fit) if frozen_fit is not None else None

    result = _ORIGINAL_UPDATE_GEOREFERENCE(map_geometry, location)
    if frozen_state is None or frozen_fit_copy is None:
        # The base learner owns the initial >=5-sample fit. Annotate its stage
        # immediately so diagnostics explain that this is an early usable fit.
        calibration = map_geometry.get("_georeference_calibration")
        if isinstance(calibration, dict):
            sample_count = len(calibration.get("samples") or [])
            calibration["refinement_stage"] = _refinement_stage(sample_count)
            calibration["refinement_sample_count"] = sample_count
            fit = calibration.get("fit")
            if isinstance(fit, dict) and _georeference.georeference_is_valid(fit):
                fit["refinement_stage"] = _refinement_stage(sample_count)
                active = map_geometry.get("georeference")
                if isinstance(active, dict):
                    active["refinement_stage"] = _refinement_stage(sample_count)
                    active["calibration"] = _georeference._calibration_summary(  # noqa: SLF001
                        calibration
                    )
        return result

    # Continue collecting only genuinely new movement samples.  The base
    # learner's >=1.5 m local separation rejects stationary GPS wander.
    sample_added = _append_refinement_sample(frozen_state, location)
    active_fit = frozen_fit_copy
    if sample_added:
        active_fit = _refine_fit_if_due(frozen_state, active_fit)
    else:
        sample_count = len(frozen_state.get("samples") or [])
        frozen_state["refinement_stage"] = _refinement_stage(sample_count)
        frozen_state["refinement_sample_count"] = sample_count

    # Preserve the fitted map transform through transient cloud-GPS drift.  A
    # changed map revision still resets the whole learner through the base path.
    validated = _georeference.validate_georeference(
        active_fit,
        location,
        limit_m=_LEARNED_VALIDATION_LIMIT_M,
    ) or active_fit
    validation = dict((validated.get("validation") or {}))
    previous_mismatches = int(frozen_state.get("mismatch_count") or 0)
    if validation.get("valid") is False:
        frozen_state["mismatch_count"] = previous_mismatches + 1
    elif validation.get("valid") is True:
        frozen_state["mismatch_count"] = 0
    else:
        frozen_state["mismatch_count"] = previous_mismatches
    frozen_state["last_validation"] = validation
    frozen_state["fit"] = deepcopy(active_fit)
    map_geometry["_georeference_calibration"] = frozen_state

    sample_count = len(frozen_state.get("samples") or [])
    stage = _refinement_stage(sample_count)
    active = dict(validated)
    active["schema_version"] = 2
    active["source"] = "cloud_location_fit"
    active["status"] = "validated"
    active["map_revision"] = revision
    active["refinement_stage"] = stage
    active["calibration"] = _georeference._calibration_summary(  # noqa: SLF001
        frozen_state
    )
    active["calibration"]["refinement_stage"] = stage
    active["calibration"]["refinement_sample_count"] = sample_count
    if frozen_state.get("last_refinement_result") is not None:
        active["calibration"]["last_refinement_result"] = frozen_state[
            "last_refinement_result"
        ]
    if frozen_state.get("last_refinement_sample_count") is not None:
        active["calibration"]["last_refinement_sample_count"] = frozen_state[
            "last_refinement_sample_count"
        ]

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


def install_georeference_semantics() -> None:
    """Route the coordinator through the staged revision-stable policy once."""
    if getattr(_georeference, "_stable_revision_fit_installed", False):
        return

    # Coordinator semantics imports update_georeference directly, so update both
    # the source module and that bound runtime reference. Import the HA-dependent
    # coordinator lazily so the pure policy remains usable in lightweight tests.
    from . import coordinator_semantics as _coordinator_semantics

    _georeference.update_georeference = stable_update_georeference
    _coordinator_semantics.update_georeference = stable_update_georeference
    _georeference._stable_revision_fit_installed = True


__all__ = ["install_georeference_semantics", "stable_update_georeference"]
