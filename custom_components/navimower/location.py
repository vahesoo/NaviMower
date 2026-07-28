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

from typing import Any


def location_topic(device_id: str) -> str:
    """Cloud MQTT topic that carries real-time pose/zone for a device."""
    return f"/downlink/vehicle/{device_id}/realtimeDate/location"


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


def parse_location_payload(
    cache: dict[str, dict], device_id: str, data: Any
) -> dict | None:
    """Merge one location message into the per-device cache.

    Fields persist across messages (a pose update keeps the last-known zone).
    Returns the updated record, or None if nothing relevant changed.
    """
    if not isinstance(data, list):
        return None
    loc = dict(cache.get(device_id) or {})
    loc["device_id"] = device_id
    changed = False
    for item in data:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == 1:
            try:
                loc["x"] = float(item["postureX"])
                loc["y"] = float(item["postureY"])
                loc["theta"] = float(item["postureTheta"])
            except (TypeError, ValueError, KeyError):
                pass
            if "vehicleState" in item:
                loc["vehicle_state"] = item["vehicleState"]
            if "time" in item:
                loc["pose_time"] = item["time"]
            changed = True
        elif t == 2:
            # Live physical-mowing progress. currentMowBoundary is the
            # partition the mower is actually mowing now (works for "mow all"
            # too); currentMowProgress is route progress (0-10000, hits 10000
            # at completion -- planned-path progress, not area coverage %).
            if "currentMowBoundary" in item:
                loc["mow_boundary"] = item.get("currentMowBoundary")
            if "currentMowProgress" in item:
                loc["mow_progress"] = item.get("currentMowProgress")
            changed = True
        elif t == 3:
            pids = item.get("partitionIds")
            loc["partition_ids"] = pids
            loc["partition"] = pids[0] if isinstance(pids, list) and pids else None
            changed = True
        elif t == 4:
            loc["task_delay"] = item.get("taskDelay")
            changed = True
    if not changed:
        return None
    cache[device_id] = loc
    return loc
