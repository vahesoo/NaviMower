"""Evidence-first mower capability profile for diagnostics and future entities.

This profile deliberately does not prune general sensors.  It records only
positive observations plus narrow, explicitly known model-family constraints so
later entity provisioning can become capability-driven without equating one
empty/transient endpoint response with "unsupported".
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import coordinator as _coordinator
from .model_support import is_h1_generation

CAPABILITY_SCHEMA_VERSION = 1

_SETTING_GROUPS: dict[str, frozenset[str]] = {
    "schedule_control": frozenset({"startPlan", "workPlanV2"}),
    "battery_limits": frozenset({"returnBatteryLevel", "chargingLimit"}),
    "weather_settings": frozenset(
        {
            "rainSensor",
            "rainDetectionSwitch",
            "weatherSwitch",
            "weatherSensitivity",
            "delayedPileSwitch",
            "delayedPileSet",
            "frostSwitch",
            "frostDelayTime",
            "snowSwitch",
            "snowDelayTime",
            "stormSwitch",
            "highTempSwitch",
            "allowMaxTemp",
        }
    ),
    "vision_settings": frozenset(
        {"slamSwitch", "cptSwitch", "animalProtection", "nightAnimalProtection"}
    ),
    "lighting_settings": frozenset(
        {"lightSwitch", "lightIntensity", "nightLightLevel", "headlightSwitch"}
    ),
    "terrain_settings": frozenset(
        {
            "tractionControl",
            "tcsSwitch",
            "terrainAdaptSwitch",
            "edgeSense",
            "edgeSenselevel",
            "narrowZoneAdaptSwitch",
            "advancedSlopeMode",
        }
    ),
    "anti_theft_settings": frozenset({"guard", "antiTheftRadius"}),
}

_ENDPOINT_KEYS = (
    "index2",
    "auth_item",
    "location",
    "set_list",
    "maintenance",
    "path_info_time",
    "device_info",
)


def _endpoint_state(raw: dict[str, Any], key: str) -> str:
    if key not in raw:
        return "missing"
    value = raw.get(key)
    if value in (None, "", [], {}):
        return "empty"
    return "present"


def _key_paths(value: Any, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.add(path)
            paths.update(_key_paths(child, path))
    elif isinstance(value, list):
        for child in value[:8]:
            paths.update(_key_paths(child, prefix))
    return paths


def _leaf_keys(paths: set[str]) -> set[str]:
    return {path.rsplit(".", 1)[-1] for path in paths}


def _has_xy(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return value.get("x") is not None and value.get("y") is not None


def _maintenance_has_value(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(item is not None for item in value.values())


def _observed(
    entries: dict[str, dict[str, Any]],
    name: str,
    *,
    source: str,
    evidence: str,
) -> None:
    item = entries.setdefault(
        name,
        {"supported": True, "sources": [], "evidence": []},
    )
    if source not in item["sources"]:
        item["sources"].append(source)
    if evidence not in item["evidence"]:
        item["evidence"].append(evidence)


def _merge_observed(
    previous: dict[str, Any] | None,
    current: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    old = (previous or {}).get("observed") if isinstance(previous, dict) else None
    if isinstance(old, dict):
        for name, item in old.items():
            if isinstance(item, dict) and item.get("supported") is True:
                merged[str(name)] = deepcopy(item)
    for name, item in current.items():
        target = merged.setdefault(
            name,
            {"supported": True, "sources": [], "evidence": []},
        )
        for key in ("sources", "evidence"):
            values = target.setdefault(key, [])
            for value in item.get(key) or []:
                if value not in values:
                    values.append(value)
        target["supported"] = True
    return merged


def build_capability_profile(
    snapshot: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a positive-evidence capability snapshot without guessing absence."""
    raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
    set_list = raw.get("set_list") if isinstance(raw.get("set_list"), dict) else {}
    key_paths = _key_paths(set_list)
    leaves = _leaf_keys(key_paths)
    observed: dict[str, dict[str, Any]] = {}

    if _endpoint_state(raw, "index2") == "present" or _endpoint_state(raw, "auth_item") == "present":
        _observed(observed, "core_status", source="private_cloud", evidence="index2_or_auth_item")
    if snapshot.get("battery_private_cloud") is not None or snapshot.get("battery_mqtt") is not None:
        _observed(observed, "battery", source="reported_value", evidence="battery_value")
    if "workPlanV2" in leaves or "startPlan" in leaves or snapshot.get("schedule"):
        _observed(observed, "schedule", source="private_set_list", evidence="workPlanV2_or_startPlan")
    map_data = snapshot.get("map")
    if isinstance(map_data, dict) and (map_data.get("zones") or map_data.get("area") is not None):
        _observed(observed, "map_geometry", source="private_map", evidence="decoded_map")
    if snapshot.get("zones"):
        _observed(observed, "zones", source="private_map_or_fallback", evidence="zone_list")
    if isinstance(snapshot.get("coverage"), dict) or _endpoint_state(raw, "path_info_time") == "present":
        _observed(observed, "coverage", source="private_cloud", evidence="path_info_time_or_coverage")
    if _maintenance_has_value(snapshot.get("maintenance")):
        _observed(observed, "maintenance", source="private_cloud", evidence="maintenance_values")
    if _has_xy(snapshot.get("cloud_position")):
        _observed(observed, "private_position", source="private_cloud", evidence="cloud_xy")
    if snapshot.get("pose_source") == "mqtt" or snapshot.get("mqtt_pose_age") is not None:
        _observed(observed, "mqtt_live_position", source="official_mqtt", evidence="mqtt_pose")
    if snapshot.get("cutting_height_supported") is True:
        _observed(observed, "cutting_height", source="reported_capability", evidence="supported_height_data")

    for capability, keys in _SETTING_GROUPS.items():
        matched = sorted(keys & leaves)
        if matched:
            _observed(
                observed,
                capability,
                source="private_set_list",
                evidence=",".join(matched),
            )

    model = str(snapshot.get("model") or "").strip()
    vehicle_type = snapshot.get("vehicle_type")
    constraints: dict[str, Any] = {}
    if is_h1_generation(model, vehicle_type):
        constraints["ordered_zone_mowing"] = {
            "supported": False,
            "source": "known_h1_model_family",
            "evidence": model or f"vehicle_type:{vehicle_type}",
        }

    return {
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "policy": "positive_evidence_only",
        "model": model or None,
        "vehicle_type": vehicle_type,
        "endpoints": {key: _endpoint_state(raw, key) for key in _ENDPOINT_KEYS},
        "setting_key_paths": sorted(key_paths),
        "observed": _merge_observed(previous, observed),
        "constraints": constraints,
    }


def install_capability_profile() -> None:
    """Decorate parsed snapshots with a sticky-in-process capability profile."""
    cls = _coordinator.NavimowCoordinator
    if getattr(cls, "_capability_profile_installed", False):
        return

    original_parse = cls._parse

    def parse(self: Any, raw: dict[str, Any]) -> dict[str, Any]:
        snapshot = original_parse(self, raw)
        previous = getattr(self, "_capability_profile", None)
        profile = build_capability_profile(snapshot, previous)
        self._capability_profile = profile
        snapshot["capabilities"] = deepcopy(profile)
        return snapshot

    cls._parse = parse
    cls._capability_profile_installed = True
