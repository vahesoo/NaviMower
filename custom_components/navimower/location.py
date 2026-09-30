"""Real-time location / zone decoding for Navimow (fork addition).

The stock navimow-sdk subscribes to the .../realtimeDate/state, /event and
/attributes MQTT channels but NOT /location, and its router drops the location
payload (a JSON array, not a dict). This module decodes that topic so the
integration can expose live position and the current mowing zone.

Observed payload: a JSON array of objects keyed by ``type``:
  type 1  pose     {postureX, postureY (meters), postureTheta (radians), vehicleState, time}
  type 2  progress {currentMowBoundary (live physical partition id), currentMowProgress
                    (route progress 0-10000, reaches 10000 at completion), mapWorkPosition}
  type 3  zone     {partitionIds: [int]}   -> the TARGET partition (set at task start;
                    absent for a "mow all" command)
  type 4  delay    {taskDelay: bool}       -> rain / schedule delay
NOTE: type 3 = target zone (drives gate pre-open); type 2 currentMowBoundary = the
live physical zone (updates only after the mower crosses). They are kept separate.
Coordinates are a local Cartesian grid in METERS whose origin is ~the dock /
RTK reference (NOT latitude/longitude).
"""
from __future__ import annotations

import math
from typing import Any


def extract_mqtt_battery(data: Any) -> int | None:
    """Extract a trustworthy 0..100 battery percentage from MQTT payloads."""
    if not isinstance(data, dict):
        return None

    def normalize(value: Any) -> int | None:
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            return None
        return parsed if 0 <= parsed <= 100 else None

    direct = normalize(data.get("battery"))
    if direct is not None:
        return direct

    capacity = data.get("capacityRemaining")
    if isinstance(capacity, list):
        fallback = None
        for item in capacity:
            if not isinstance(item, dict):
                continue
            value = normalize(item.get("rawValue"))
            if value is None:
                continue
            if str(item.get("unit") or "").upper() == "PERCENTAGE":
                return value
            if fallback is None:
                fallback = value
        if fallback is not None:
            return fallback

    descriptive = data.get("descriptiveCapacityRemaining")
    if isinstance(descriptive, dict):
        for key in ("rawValue", "value", "percentage"):
            value = normalize(descriptive.get(key))
            if value is not None:
                return value
    return normalize(descriptive)


def location_topic(device_id: str) -> str:
    """Cloud MQTT topic that carries real-time pose/zone for a device."""
    return f"/downlink/vehicle/{device_id}/realtimeDate/location"


def decode_map_work_position(value: Any) -> dict[str, int] | None:
    """Decode observed 32-bit big-endian map-work-position words.

    The fourth word tracks the mower's immediate target partition even when
    ``partitionIds`` contains every zone selected for a multi-zone task.  The
    first two words mirror the current action/sub-action; the fifth is an
    observed work-progress counter. Unknown trailing words are intentionally
    ignored.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if len(raw) < 40 or len(raw) % 8:
        return None
    try:
        words = [
            int.from_bytes(bytes.fromhex(raw[index : index + 8]), "big", signed=True)
            for index in range(0, min(len(raw), 40), 8)
        ]
    except ValueError:
        return None
    if len(words) < 5:
        return None
    return {
        "action": words[0],
        "sub_action": words[1],
        "mode": words[2],
        "target_zone": words[3],
        "progress": words[4],
    }


# Mower status values during which the pose is the dock position. "idle" is
# deliberately excluded: the mower can sit idle mid-lawn after a manual stop.
DOCKED_STATES = frozenset({"docked", "charging"})

# Cap on the effective sample count for the dock average. Once reached, new
# samples keep a constant 1/DOCK_MAX_SAMPLES weight, so the estimate tracks a
# physically moved dock instead of being frozen by historical samples.
DOCK_MAX_SAMPLES = 200


def update_dock_estimate(
    dock: dict | None, x: float, y: float, max_samples: int = DOCK_MAX_SAMPLES
) -> dict:
    """Fold one docked pose sample into the running dock-position average.

    Returns a new dict {"x", "y", "n"}; pass the previous result (or None)
    as ``dock``. The capped incremental mean smooths RTK jitter while still
    converging on a new location if the dock is moved.
    """
    d = dock or {"x": 0.0, "y": 0.0, "n": 0}
    n = min(int(d.get("n", 0)), max_samples - 1)
    return {
        "x": (d["x"] * n + float(x)) / (n + 1),
        "y": (d["y"] * n + float(y)) / (n + 1),
        "n": n + 1,
    }


def _normalize_source_time(value: Any) -> float | None:
    """Normalize vendor source time to epoch-like seconds for ordering only."""
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    # Observed location timestamps are usually epoch milliseconds. Keep epoch
    # seconds unchanged and normalize milliseconds so diagnostics are readable.
    return parsed / 1000.0 if parsed > 10_000_000_000 else parsed


def _message_source_time(item: dict[str, Any]) -> float | None:
    """Return the best source timestamp carried by one location sub-message."""
    for key in ("time", "timestamp", "reportTime", "report_time"):
        if key in item:
            parsed = _normalize_source_time(item.get(key))
            if parsed is not None:
                return parsed
    return None


def _accept_source_order(
    source_times: dict[str, float],
    late_counts: dict[str, int],
    item: dict[str, Any],
) -> tuple[bool, int | None, float | None]:
    """Reject a source-time regression independently for each message type."""
    try:
        message_type = int(item.get("type"))
    except (TypeError, ValueError):
        return True, None, None
    if message_type not in {1, 2, 3, 4}:
        return True, message_type, None

    source_time = _message_source_time(item)
    if source_time is None:
        # Compatibility path for mower/firmware variants that omit source time.
        return True, message_type, None

    key = str(message_type)
    latest = _normalize_source_time(source_times.get(key))
    if latest is not None and source_time < latest:
        late_counts[key] = int(late_counts.get(key) or 0) + 1
        return False, message_type, source_time

    if latest is None or source_time > latest:
        source_times[key] = source_time
    return True, message_type, source_time


def parse_location_payload(
    cache: dict[str, dict], device_id: str, data: Any
) -> dict | None:
    """Merge one ordered location message into the per-device cache.

    Fields persist across messages, but each vendor sub-message type owns an
    independent source-time high-water mark. Delayed/out-of-order type 1/2/3/4
    entries are rejected before they can overwrite newer semantic state.
    """
    if not isinstance(data, list):
        return None
    loc = dict(cache.get(device_id) or {})
    loc["device_id"] = device_id
    source_times = dict(loc.get("_source_time_by_type") or {})
    late_counts = {
        str(key): int(value or 0)
        for key, value in dict(loc.get("_late_rejected_by_type") or {}).items()
    }
    loc["_source_time_by_type"] = source_times
    loc["_late_rejected_by_type"] = late_counts
    loc["_accepted_message_types"] = []
    loc["_late_rejected_message_types"] = []

    # These flags describe only fields accepted from this MQTT message. Cached
    # values remain available, but must not become fresh again accidentally.
    for flag in (
        "_pose_updated",
        "_progress_updated",
        "_route_progress_updated",
        "_work_progress_updated",
        "_task_progress_updated",
        "_area_updated",
        "_battery_updated",
        "_task_delay_updated",
        "_state_updated",
        "_action_updated",
        "_partition_ids_updated",
        "_work_target_updated",
    ):
        loc[flag] = False

    changed = False
    for item in data:
        if not isinstance(item, dict):
            continue
        accepted, t, _source_time = _accept_source_order(
            source_times, late_counts, item
        )
        if not accepted:
            if t is not None:
                loc["_late_rejected_message_types"].append(t)
            continue
        if t is not None:
            loc["_accepted_message_types"].append(t)

        if t == 1:
            try:
                loc["x"] = float(item["postureX"])
                loc["y"] = float(item["postureY"])
                loc["theta"] = float(item["postureTheta"])
            except (TypeError, ValueError, KeyError):
                pass
            if "vehicleState" in item:
                loc["vehicle_state"] = item["vehicleState"]
                loc["_state_updated"] = True
            if "time" in item:
                loc["pose_time"] = item["time"]
            if (battery := extract_mqtt_battery(item)) is not None:
                loc["battery"] = battery
                loc["_battery_updated"] = True
            loc["_pose_updated"] = True
            changed = True
        elif t == 2:
            if "currentMowBoundary" in item:
                loc["mow_boundary"] = item.get("currentMowBoundary")
            if "currentMowProgress" in item:
                loc["mow_progress"] = item.get("currentMowProgress")
                loc["_progress_updated"] = True
                loc["_route_progress_updated"] = True
            if "action" in item:
                loc["action"] = item.get("action")
                loc["_action_updated"] = True
            if "subAction" in item:
                loc["sub_action"] = item.get("subAction")
                loc["_action_updated"] = True
            if "mapWorkPosition" in item:
                loc["map_work_position"] = item.get("mapWorkPosition")
                decoded = decode_map_work_position(item.get("mapWorkPosition"))
                if decoded is not None:
                    loc["work_action"] = decoded["action"]
                    loc["work_sub_action"] = decoded["sub_action"]
                    loc["work_mode"] = decoded["mode"]
                    loc["work_target_zone"] = decoded["target_zone"]
                    loc["work_progress"] = decoded["progress"]
                    loc["_progress_updated"] = True
                    loc["_work_progress_updated"] = True
                    loc["_work_target_updated"] = True
                    if "action" not in item:
                        loc["action"] = decoded["action"]
                        loc["_action_updated"] = True
                    if "subAction" not in item:
                        loc["sub_action"] = decoded["sub_action"]
                        loc["_action_updated"] = True
            if "mowStartType" in item:
                loc["mow_start_type"] = item.get("mowStartType")
            if "mowingPercentage" in item:
                loc["mowing_percentage"] = item.get("mowingPercentage")
                loc["_progress_updated"] = True
                loc["_task_progress_updated"] = True
            if "subtotalArea" in item:
                loc["subtotal_area"] = item.get("subtotalArea")
                loc["_area_updated"] = True
            if "mowingWeekArea" in item:
                loc["mowing_week_area"] = item.get("mowingWeekArea")
                loc["_area_updated"] = True
            if (battery := extract_mqtt_battery(item)) is not None:
                loc["battery"] = battery
                loc["_battery_updated"] = True
            changed = True
        elif t == 3:
            pids = item.get("partitionIds")
            loc["partition_ids"] = pids
            loc["partition"] = pids[0] if isinstance(pids, list) and pids else None
            loc["_partition_ids_updated"] = "partitionIds" in item
            changed = True
        elif t == 4:
            loc["task_delay"] = item.get("taskDelay")
            loc["_task_delay_updated"] = "taskDelay" in item
            if "vehicleState" in item:
                loc["vehicle_state"] = item.get("vehicleState")
                loc["_state_updated"] = True
            if "time" in item:
                loc["state_time"] = item.get("time")
            changed = True

    # Persist ordering metadata even if every semantic item was rejected so the
    # next packet and diagnostics see the correct counters/high-water marks.
    cache[device_id] = loc
    if not changed:
        return None
    return loc
