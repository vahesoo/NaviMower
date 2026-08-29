"""Model-aware capability hardening layered over the shared vendor schema.

Navimow returns many dormant/default ``set-list`` keys on models that do not
actually expose the corresponding app feature.  Keep raw discovery available in
diagnostics, but gate user-facing controls with documented/model-tested evidence.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from . import coordinator as _coordinator
from .model_capabilities import (
    FAMILY_H2,
    FAMILY_I1,
    FAMILY_X3,
    capability_profile,
    model_family,
)

_REPORTED_ONLY_GROUPS = {
    "schedule_control",
    "battery_limits",
    "weather_settings",
    "vision_settings",
    "lighting_settings",
    "terrain_settings",
    "anti_theft_settings",
}

# These i2-AWD controls were originally exposed from field presence while we
# were investigating the family.  Keep them hidden until a manual/app capture or
# safe before/after write test proves the feature and its exact semantics.
_WAIT_FOR_FIELD_EVIDENCE_SWITCHES = {
    "advanced_slope_mode",
    "headlight",
    "narrow_zone_adapt",
    "night_animal_protection",
    "progress_retention",
}
_WAIT_FOR_FIELD_EVIDENCE_NUMBERS = {"progress_retention_duration"}
_WAIT_FOR_FIELD_EVIDENCE_SELECTS = {"positioning_mode"}


def _raw(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("raw")
    return value if isinstance(value, dict) else {}


def _set_list(data: dict[str, Any]) -> dict[str, Any]:
    value = _raw(data).get("set_list")
    return value if isinstance(value, dict) else {}


def _device_info(data: dict[str, Any]) -> dict[str, Any]:
    value = _raw(data).get("device_info")
    return value if isinstance(value, dict) else {}


def _battery_config(data: dict[str, Any]) -> dict[str, Any]:
    nonstandard = _device_info(data).get("nonstandardVehicleConfig")
    if not isinstance(nonstandard, dict):
        return {}
    config = nonstandard.get("batteryConfig")
    return config if isinstance(config, dict) else {}


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _height_options(data: dict[str, Any]) -> list[int]:
    values = _device_info(data).get("mowingHeightList")
    result: list[int] = []
    for value in values if isinstance(values, list) else []:
        parsed = _as_int(value)
        if parsed is not None and 10 <= parsed <= 150 and parsed not in result:
            result.append(parsed)
    return sorted(result)


def _family_profile(data: dict[str, Any]):
    return capability_profile(data.get("model"), _as_int(data.get("vehicle_type")))


def _strip_untrusted_i1_height(snapshot: dict[str, Any]) -> None:
    """Keep i1 physical range metadata without claiming knob position is known."""
    if model_family(snapshot.get("model"), _as_int(snapshot.get("vehicle_type"))) != FAMILY_I1:
        return

    snapshot["cutting_height_supported"] = False
    snapshot["active_cutting_height_mm"] = None
    settings = dict(snapshot.get("settings") or {})
    settings["cut_height"] = None
    settings["cutting_height_supported"] = False
    # Preserve cut_height_raw for diagnostics: it is exactly the evidence we
    # still want to compare with a future physical-knob before/after test.
    snapshot["settings"] = settings

    for detail in snapshot.get("zone_details") or []:
        if not isinstance(detail, dict):
            continue
        detail["cutting_height_supported"] = False
        detail["configured_height_mm"] = None
        detail["cutting_height_mm"] = None
        detail["inherits_global_height"] = None

    map_data = snapshot.get("map")
    if isinstance(map_data, dict):
        for zone in map_data.get("zones") or []:
            if not isinstance(zone, dict):
                continue
            boundary = zone.get("boundary")
            if isinstance(boundary, dict):
                boundary.pop("height_set", None)

    for session in snapshot.get("sessions") or []:
        if isinstance(session, dict):
            session["cutting_height_mm"] = None


def _feature_entry(
    data: dict[str, Any],
    *,
    vendor_field: str,
    documented: bool,
) -> dict[str, Any]:
    settings = _set_list(data)
    reported = vendor_field in settings
    return {
        "vendor_field": vendor_field,
        "reported": reported,
        "documented_for_family": documented,
        "readable": bool(documented and reported),
        "writable": bool(documented and reported),
        "reported_value": settings.get(vendor_field) if reported else None,
        "waiting_for_firmware_or_field_evidence": bool(reported and not documented),
    }


def _height_capability(data: dict[str, Any]) -> dict[str, Any]:
    family = _family_profile(data)
    options = _height_options(data)
    raw_height = _as_int(_set_list(data).get("height"))
    if options:
        physical_range = [min(options), max(options)]
        range_source = "device_info.mowingHeightList"
    elif family.cutting_height_range_mm is not None:
        physical_range = list(family.cutting_height_range_mm)
        range_source = "official_family_fallback"
    else:
        physical_range = None
        range_source = None
    return {
        "adjustment": family.cutting_height_adjustment,
        "physical_range_mm": physical_range,
        "range_source": range_source,
        "reported_options_mm": options,
        "reported_current_value": raw_height,
        "readable_current_value": bool(
            family.cutting_height_readable and raw_height is not None
        ),
        "writable": bool(family.cutting_height_writable and options),
        "global_control": bool(family.cutting_height_writable and options),
        "manual_current_value_pending_validation": family.family == FAMILY_I1,
        "evidence": [
            value
            for value in (
                "official_family_behavior" if family.cutting_height_adjustment else None,
                "device_info.mowingHeightList" if options else None,
                "set_list.height" if raw_height is not None else None,
            )
            if value is not None
        ],
    }


def _battery_limits_capability(data: dict[str, Any]) -> dict[str, Any]:
    settings = _set_list(data)
    config = _battery_config(data)
    return_min = _as_int(config.get("returnBatteryLevelMin"))
    return_max = _as_int(config.get("returnBatteryLevelMax"))
    charge_min = _as_int(config.get("chargingLimitMin"))
    charge_max = _as_int(config.get("chargingLimitMax"))
    bounds_available = None not in (return_min, return_max, charge_min, charge_max)
    return {
        "reported": bool(
            "returnBatteryLevel" in settings or "chargingLimit" in settings
        ),
        "current_return_level": _as_int(settings.get("returnBatteryLevel")),
        "current_charging_limit": _as_int(settings.get("chargingLimit")),
        "vendor_bounds_available": bounds_available,
        "return_range_pct": (
            [return_min, return_max] if None not in (return_min, return_max) else None
        ),
        "charging_range_pct": (
            [charge_min, charge_max] if None not in (charge_min, charge_max) else None
        ),
        "range_source": (
            "device_info.nonstandardVehicleConfig.batteryConfig"
            if bounds_available
            else "entity_safe_fallback_not_vendor_claim"
        ),
    }


def _harden_capability_profile(
    snapshot: dict[str, Any], profile: dict[str, Any] | None
) -> dict[str, Any]:
    hardened = deepcopy(profile) if isinstance(profile, dict) else {}
    family = _family_profile(snapshot)
    hardened["schema_version"] = 2
    hardened["policy"] = "reported_fields_are_not_remote_capability_proof"
    hardened["model_family"] = family.family

    observed = hardened.get("observed")
    if isinstance(observed, dict):
        for name in _REPORTED_ONLY_GROUPS:
            item = observed.get(name)
            if isinstance(item, dict):
                item["reported"] = True
                item["supported"] = None

    settings = _set_list(snapshot)
    hardened["settings"] = {
        "cutting_height": _height_capability(snapshot),
        "battery_limits": _battery_limits_capability(snapshot),
        "mowing_speed": {
            "vendor_field": "mode",
            "reported": "mode" in settings,
            "readable": "mode" in settings,
            "writable": "mode" in settings,
            "reported_value": settings.get("mode"),
            "meaning": "mower_movement_speed_during_mowing",
        },
        "lawn_mowing_efficiency": {
            "vendor_field": "lawnMowingEfficiency",
            "reported": "lawnMowingEfficiency" in settings,
            "reported_value": settings.get("lawnMowingEfficiency"),
            "relationship_to_mowing_speed_pending_validation": True,
        },
        "grass_pattern_enhancement": _feature_entry(
            snapshot,
            vendor_field="grassPatternEnhancement",
            documented=family.grass_pattern_enhancement,
        ),
        "traction_control": _feature_entry(
            snapshot,
            vendor_field="tcsSwitch",
            documented=family.traction_control,
        ),
        "terrain_adapt": _feature_entry(
            snapshot,
            vendor_field="terrainAdaptSwitch",
            documented=family.terrain_adapt,
        ),
        "edge_sense": _feature_entry(
            snapshot,
            vendor_field="edgeSense",
            documented=family.edge_sense,
        ),
    }
    return hardened


def _install_snapshot_semantics() -> None:
    cls = _coordinator.NavimowCoordinator
    if getattr(cls, "_model_capability_semantics_installed", False):
        return
    original_parse = cls._parse

    def parse(self: Any, raw: dict[str, Any]) -> dict[str, Any]:
        snapshot = original_parse(self, raw)
        snapshot["model_family"] = model_family(
            snapshot.get("model"), _as_int(snapshot.get("vehicle_type"))
        )
        _strip_untrusted_i1_height(snapshot)
        hardened = _harden_capability_profile(snapshot, snapshot.get("capabilities"))
        snapshot["capabilities"] = hardened
        self._capability_profile = deepcopy(hardened)
        return snapshot

    cls._parse = parse
    cls._model_capability_semantics_installed = True


def _install_switch_semantics() -> None:
    from . import switch as platform

    original_model_supported = platform._model_supported

    def model_supported(description: Any, data: dict[str, Any]) -> bool:
        family = _family_profile(data)
        key = description.key
        if key == "traction_control":
            return family.traction_control
        if key == "terrain_adapt":
            return family.terrain_adapt
        if key == "edge_sense":
            return family.edge_sense
        if key == "grass_pattern_enhancement":
            return family.grass_pattern_enhancement
        if key in _WAIT_FOR_FIELD_EVIDENCE_SWITCHES:
            return False
        return original_model_supported(description, data)

    platform._model_supported = model_supported


def _install_select_semantics() -> None:
    from . import select as platform

    rewritten = []
    for description in platform.SETTING_SELECTS:
        if description.key == "work_mode":
            description = replace(
                description,
                name="Mowing speed",
                translation_key=None,
                icon="mdi:speedometer",
            )
        rewritten.append(description)
    platform.SETTING_SELECTS = tuple(rewritten)

    original_model_supported = platform._model_supported

    def model_supported(description: Any, data: dict[str, Any]) -> bool:
        family = _family_profile(data)
        key = description.key
        if key == "night_light_level":
            return family.family == FAMILY_H2
        if key == "light_brightness":
            return family.family == FAMILY_X3
        if key == "edge_sense_mode":
            return family.edge_sense
        if key in _WAIT_FOR_FIELD_EVIDENCE_SELECTS:
            return False
        return original_model_supported(description, data)

    platform._model_supported = model_supported


def _install_number_semantics() -> None:
    from . import number as platform

    # capability_extensions intentionally tightened these while i2 AWD was under
    # investigation.  When batteryConfig is absent that would falsely present a
    # vendor-defined range.  Restore the broad safe entity fallback; the existing
    # entity initializer still replaces it with exact batteryConfig bounds when
    # the mower supplies them.
    rewritten = []
    for description in platform.NUMBERS:
        if description.key == "return_battery_level":
            description = replace(
                description,
                native_min_value=5,
                native_max_value=50,
                native_step=5,
            )
        elif description.key == "charging_limit":
            description = replace(
                description,
                native_min_value=50,
                native_max_value=100,
                native_step=5,
            )
        rewritten.append(description)
    platform.NUMBERS = tuple(rewritten)

    original_wire_value = platform._wire_value

    def wire_value(description: Any, data: dict[str, Any]) -> int | None:
        if description.key == "cutting_height":
            family = _family_profile(data)
            if not family.cutting_height_writable or not _height_options(data):
                return None
        if description.key in _WAIT_FOR_FIELD_EVIDENCE_NUMBERS:
            return None
        return original_wire_value(description, data)

    platform._wire_value = wire_value


def install_capability_semantics() -> None:
    """Install conservative model-family gates after legacy capability patches."""
    cls = _coordinator.NavimowCoordinator
    if getattr(cls, "_capability_semantics_installed", False):
        return
    _install_snapshot_semantics()
    _install_switch_semantics()
    _install_select_semantics()
    _install_number_semantics()
    cls._capability_semantics_installed = True


__all__ = ["install_capability_semantics"]
