"""Keep map georeference stable while refining it from wider movement samples.

The first usable cloud-location fit is intentionally available early so map
consumers do not have to wait for a whole mowing cycle. For the same map
revision we keep collecting spatially separated local-X/Y + WGS84 pairs and
improve the transform in stages:

* provisional: the existing first fit at >=5 samples;
* refined: re-fit at 8, 9 and 10 accepted samples;
* high confidence: re-fit at 12, 16, 20 and 24 accepted samples;
* adaptive spatial refinement: after 24 samples, a genuinely better spatial
  candidate may replace one redundant sample and trigger a new fit.

The adaptive stage does not become a rolling latest-24 window. A new point is
accepted only when replacing one existing point measurably improves the spatial
coverage of the retained set and the resulting fit remains healthy. This lets a
mower gradually improve calibration as it reaches farther parts of the lawn
without letting a short recent mowing segment move an already good transform.

A validated transform is never thrown away merely because later stationary
cloud GPS validation drifts; map revision remains the authoritative reset
trigger.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from . import georeference as _georeference

_LEARNED_VALIDATION_LIMIT_M = 3.0
_REFINED_SAMPLE_COUNTS = frozenset({8, 9, 10})
_HIGH_CONFIDENCE_MIN_SAMPLES = 12
_HIGH_CONFIDENCE_REFIT_COUNTS = frozenset({12, 16, 20, 24})
_MAX_REFINEMENT_SAMPLES = 24
_PROVISIONAL_SEPARATION_M = 1.5
_REFINED_SEPARATION_M = 2.0
_HIGH_CONFIDENCE_SEPARATION_M = 3.0
_MIN_SPATIAL_SCORE_GAIN_M = 0.75
_MIN_SPATIAL_SCORE_GAIN_RATIO = 0.015
_MAX_RMS_DEGRADATION_M = 0.25
_MAX_RMS_DEGRADATION_RATIO = 1.35
_MAX_MAX_ERROR_DEGRADATION_M = 0.50
_REFINEMENT_POLICY = "adaptive_spatial_v3"
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
    return (
        sample_count in _REFINED_SAMPLE_COUNTS
        or sample_count in _HIGH_CONFIDENCE_REFIT_COUNTS
    )


def _required_sample_separation(sample_count: int) -> float:
    if sample_count >= 10:
        return _HIGH_CONFIDENCE_SEPARATION_M
    if sample_count >= 5:
        return _REFINED_SEPARATION_M
    return _PROVISIONAL_SEPARATION_M


def _convex_hull_area(samples: list[dict[str, Any]]) -> float:
    points = sorted({(float(item["x"]), float(item["y"])) for item in samples})
    if len(points) < 3:
        return 0.0

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    hull = lower[:-1] + upper[:-1]
    area2 = 0.0
    for index, point in enumerate(hull):
        nxt = hull[(index + 1) % len(hull)]
        area2 += point[0] * nxt[1] - nxt[0] * point[1]
    return abs(area2) / 2.0


def _spatial_metrics(samples: list[dict[str, Any]]) -> dict[str, float]:
    baseline = _georeference._baseline_m(samples) if samples else 0.0  # noqa: SLF001
    hull_area = _convex_hull_area(samples)
    # Metre-like score: baseline rewards long lever arms while sqrt(area)
    # rewards 2-D coverage instead of allowing a single long line to dominate.
    score = baseline + 2.0 * math.sqrt(max(0.0, hull_area))
    return {
        "baseline_m": float(baseline),
        "hull_area_m2": float(hull_area),
        "score_m": float(score),
    }


def _sample_is_duplicate(samples: list[dict[str, Any]], sample: dict[str, Any]) -> bool:
    report_time = sample.get("report_time")
    return report_time is not None and any(
        existing.get("report_time") == report_time for existing in samples
    )


def _append_refinement_sample(state: dict[str, Any], location: Any) -> bool:
    """Append a normal pre-cap refinement sample."""
    sample = _georeference._current_location_sample(location)  # noqa: SLF001
    if sample is None:
        return False

    samples = [
        dict(item) for item in state.get("samples") or [] if isinstance(item, dict)
    ]
    state["samples"] = samples
    state["refinement_policy"] = _REFINEMENT_POLICY
    if len(samples) >= _MAX_REFINEMENT_SAMPLES or _sample_is_duplicate(samples, sample):
        return False

    required_separation = _required_sample_separation(len(samples))
    state["refinement_sample_separation_m"] = required_separation
    if any(
        _georeference._sample_distance_m(existing, sample) < required_separation  # noqa: SLF001
        for existing in samples
    ):
        return False

    samples.append(sample)
    return True


def _fit_quality_ok(current_fit: dict[str, Any], candidate_fit: dict[str, Any]) -> bool:
    current = current_fit.get("calibration") or {}
    candidate = candidate_fit.get("calibration") or {}
    current_rms = float(current.get("rms_error_m") or 0.0)
    candidate_rms = float(candidate.get("rms_error_m") or 0.0)
    current_max = float(current.get("max_error_m") or 0.0)
    candidate_max = float(candidate.get("max_error_m") or 0.0)
    if current_rms > 0.0:
        rms_limit = max(
            current_rms + _MAX_RMS_DEGRADATION_M,
            current_rms * _MAX_RMS_DEGRADATION_RATIO,
        )
        if candidate_rms > rms_limit:
            return False
    if current_max > 0.0 and candidate_max > current_max + _MAX_MAX_ERROR_DEGRADATION_M:
        return False
    return True


def _try_adaptive_replacement(
    state: dict[str, Any], location: Any, current_fit: dict[str, Any]
) -> dict[str, Any]:
    """Replace one redundant retained sample when coverage improves materially."""
    sample = _georeference._current_location_sample(location)  # noqa: SLF001
    samples = [
        dict(item) for item in state.get("samples") or [] if isinstance(item, dict)
    ]
    state["samples"] = samples
    if sample is None or len(samples) < _MAX_REFINEMENT_SAMPLES:
        return current_fit
    if _sample_is_duplicate(samples, sample):
        state["last_adaptive_result"] = "duplicate"
        return current_fit

    current_metrics = _spatial_metrics(samples)
    required_separation = _HIGH_CONFIDENCE_SEPARATION_M
    best_samples: list[dict[str, Any]] | None = None
    best_metrics = current_metrics
    best_removed_index: int | None = None

    for index in range(len(samples)):
        remaining = samples[:index] + samples[index + 1 :]
        if any(
            _georeference._sample_distance_m(existing, sample) < required_separation  # noqa: SLF001
            for existing in remaining
        ):
            continue
        proposed = remaining + [sample]
        metrics = _spatial_metrics(proposed)
        if metrics["score_m"] > best_metrics["score_m"]:
            best_samples = proposed
            best_metrics = metrics
            best_removed_index = index

    gain = best_metrics["score_m"] - current_metrics["score_m"]
    required_gain = max(
        _MIN_SPATIAL_SCORE_GAIN_M,
        current_metrics["score_m"] * _MIN_SPATIAL_SCORE_GAIN_RATIO,
    )
    state["last_spatial_score_gain_m"] = round(gain, 3)
    state["required_spatial_score_gain_m"] = round(required_gain, 3)
    if best_samples is None or gain < required_gain:
        state["last_adaptive_result"] = "no_material_coverage_gain"
        return current_fit

    candidate = _georeference._fit_samples(best_samples)  # noqa: SLF001
    if (
        not isinstance(candidate, dict)
        or candidate.get("status") != "validated"
        or not _fit_quality_ok(current_fit, candidate)
    ):
        state["last_adaptive_result"] = "fit_rejected"
        return current_fit

    refined = {
        key: deepcopy(value)
        for key, value in candidate.items()
        if key != "validation"
    }
    refined["refinement_stage"] = "high_confidence"
    refined["refinement_policy"] = _REFINEMENT_POLICY
    state["samples"] = best_samples
    state["fit"] = refined
    state["last_adaptive_result"] = "accepted"
    state["adaptive_replacement_count"] = int(state.get("adaptive_replacement_count") or 0) + 1
    state["last_adaptive_removed_index"] = best_removed_index
    state["last_refinement_result"] = "accepted"
    state["last_refinement_sample_count"] = len(best_samples)
    return refined


def _annotate_state(state: dict[str, Any]) -> tuple[int, str]:
    sample_count = len(state.get("samples") or [])
    stage = _refinement_stage(sample_count)
    state["refinement_policy"] = _REFINEMENT_POLICY
    state["refinement_stage"] = stage
    state["refinement_sample_count"] = sample_count
    state["refinement_locked"] = False
    state["refinement_sample_separation_m"] = _required_sample_separation(sample_count)
    metrics = _spatial_metrics(state.get("samples") or [])
    state["spatial_baseline_m"] = round(metrics["baseline_m"], 3)
    state["spatial_hull_area_m2"] = round(metrics["hull_area_m2"], 3)
    state["spatial_score_m"] = round(metrics["score_m"], 3)
    return sample_count, stage


def _refine_fit_if_due(
    state: dict[str, Any], current_fit: dict[str, Any]
) -> dict[str, Any]:
    sample_count, stage = _annotate_state(state)
    if not _should_refit(sample_count):
        return current_fit

    samples = state.get("samples") or []
    candidate = _georeference._fit_samples(samples)  # noqa: SLF001
    if not isinstance(candidate, dict) or candidate.get("status") != "validated":
        state["last_refinement_result"] = "rejected"
        state["last_refinement_sample_count"] = sample_count
        return current_fit

    refined = {
        key: deepcopy(value)
        for key, value in candidate.items()
        if key != "validation"
    }
    refined["refinement_stage"] = stage
    refined["refinement_policy"] = _REFINEMENT_POLICY
    state["fit"] = refined
    state["last_refinement_result"] = "accepted"
    state["last_refinement_sample_count"] = sample_count
    return refined


def _decorate_active(
    active: dict[str, Any], state: dict[str, Any], revision: str
) -> dict[str, Any]:
    sample_count, stage = _annotate_state(state)
    active["schema_version"] = 2
    active["source"] = "cloud_location_fit"
    active["status"] = "validated"
    active["map_revision"] = revision
    active["refinement_stage"] = stage
    active["refinement_policy"] = _REFINEMENT_POLICY
    active["calibration"] = _georeference._calibration_summary(state)  # noqa: SLF001
    active["calibration"].update(
        {
            "refinement_stage": stage,
            "refinement_policy": _REFINEMENT_POLICY,
            "refinement_sample_count": sample_count,
            "refinement_locked": False,
            "refinement_sample_separation_m": state.get("refinement_sample_separation_m"),
            "spatial_baseline_m": state.get("spatial_baseline_m"),
            "spatial_hull_area_m2": state.get("spatial_hull_area_m2"),
            "spatial_score_m": state.get("spatial_score_m"),
            "adaptive_replacement_count": int(state.get("adaptive_replacement_count") or 0),
        }
    )
    for key in (
        "last_refinement_result",
        "last_refinement_sample_count",
        "last_adaptive_result",
        "last_spatial_score_gain_m",
        "required_spatial_score_gain_m",
    ):
        if state.get(key) is not None:
            active["calibration"][key] = state[key]
    return active


def stable_update_georeference(
    map_geometry: Any, location: Any
) -> dict[str, Any] | None:
    """Update georeference with staged and adaptive spatial refinement."""
    if not isinstance(map_geometry, dict):
        return _ORIGINAL_UPDATE_GEOREFERENCE(map_geometry, location)

    revision = str(map_geometry.get("revision") or "")
    calibration_before = map_geometry.get("_georeference_calibration")
    frozen_fit = _validated_fit(calibration_before, revision)
    frozen_state = deepcopy(calibration_before) if frozen_fit is not None else None
    frozen_fit_copy = deepcopy(frozen_fit) if frozen_fit is not None else None

    result = _ORIGINAL_UPDATE_GEOREFERENCE(map_geometry, location)
    if frozen_state is None or frozen_fit_copy is None:
        calibration = map_geometry.get("_georeference_calibration")
        if isinstance(calibration, dict):
            _annotate_state(calibration)
            fit = calibration.get("fit")
            if isinstance(fit, dict) and _georeference.georeference_is_valid(fit):
                fit["refinement_stage"] = calibration["refinement_stage"]
                fit["refinement_policy"] = _REFINEMENT_POLICY
                active = map_geometry.get("georeference")
                if isinstance(active, dict):
                    active["refinement_stage"] = calibration["refinement_stage"]
                    active["refinement_policy"] = _REFINEMENT_POLICY
                    active["calibration"] = _decorate_active(
                        dict(active), calibration, revision
                    )["calibration"]
        return result

    active_fit = frozen_fit_copy
    if len(frozen_state.get("samples") or []) < _MAX_REFINEMENT_SAMPLES:
        sample_added = _append_refinement_sample(frozen_state, location)
        if sample_added:
            active_fit = _refine_fit_if_due(frozen_state, active_fit)
        else:
            _annotate_state(frozen_state)
    else:
        # A beta8 24-sample calibration migrates directly into this adaptive
        # policy; it does not need a map edit or manual reset to improve.
        active_fit = _try_adaptive_replacement(frozen_state, location, active_fit)
        _annotate_state(frozen_state)

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

    active = _decorate_active(dict(validated), frozen_state, revision)

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
    """Route the coordinator through the adaptive revision-stable policy once."""
    if getattr(_georeference, "_stable_revision_fit_installed", False):
        return

    from . import coordinator_semantics as _coordinator_semantics

    _georeference.update_georeference = stable_update_georeference
    _coordinator_semantics.update_georeference = stable_update_georeference
    _georeference._stable_revision_fit_installed = True


__all__ = ["install_georeference_semantics", "stable_update_georeference"]
