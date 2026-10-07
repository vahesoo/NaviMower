"""Stateful startup/departure trust gate for public mower position.

The normal MQTT freshness clock measures when Home Assistant received a pose.
That is correct for steady-state stream health but cannot prove that the first
type-1 packet after subscribe was produced recently by the mower. A retained or
cached first packet can therefore look "fresh" at startup even when its vendor
pose timestamp is old.

This module does not synthesize mower coordinates. It only decides whether real
MQTT/private-cloud pose candidates may be published yet. Map station geometry is
used solely as physical validation evidence while the mower is confirmed docked.
"""
from __future__ import annotations

import math
import time
from typing import Any

POSITION_TRUST_SCHEMA_VERSION = 1
DOCK_STATION_TOLERANCE_M = 2.0
SOURCE_FRESH_MAX_AGE_S = 120.0
SOURCE_FUTURE_TOLERANCE_S = 30.0
DEPARTURE_SOURCE_SKEW_S = 10.0
STARTUP_FAIL_OPEN_S = 30.0


def _as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _position(value: Any) -> dict[str, float | None] | None:
    if not isinstance(value, dict):
        return None
    x = _as_float(value.get("x"))
    y = _as_float(value.get("y"))
    if x is None or y is None:
        return None
    return {"x": x, "y": y, "heading": _as_float(value.get("heading"))}


def _source_epoch(value: Any) -> float | None:
    parsed = _as_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed / 1000.0 if parsed > 10_000_000_000 else parsed


def _source_age_s(value: Any, now_epoch: float) -> float | None:
    source = _source_epoch(value)
    if source is None:
        return None
    age = now_epoch - source
    if age < -SOURCE_FUTURE_TOLERANCE_S:
        return None
    # Small future skew is harmless for freshness decisions.
    return max(0.0, age)


def _distance(a: Any, b: Any) -> float | None:
    left = _position(a)
    right = _position(b)
    if left is None or right is None:
        return None
    return math.hypot(float(left["x"]) - float(right["x"]), float(left["y"]) - float(right["y"]))


def _station_position(owner: Any, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    map_data = snapshot.get("map") if isinstance(snapshot.get("map"), dict) else {}
    station = map_data.get("station")
    if isinstance(station, dict) and _position(station) is not None:
        return station
    geometry = getattr(owner, "_map_geometry", None)
    if isinstance(geometry, dict):
        station = geometry.get("station")
        if isinstance(station, dict) and _position(station) is not None:
            return station
    return None


def _state(owner: Any, *, now_epoch: float, now_mono: float) -> dict[str, Any]:
    state = getattr(owner, "_position_trust_state", None)
    if not isinstance(state, dict) or state.get("schema_version") != POSITION_TRUST_SCHEMA_VERSION:
        state = {
            "schema_version": POSITION_TRUST_SCHEMA_VERSION,
            "phase": "startup_waiting",
            "ready": False,
            "started_at_epoch": now_epoch,
            "started_at_mono": now_mono,
            "last_docked": None,
            "last_position": None,
            "first_trusted_source": None,
            "last_reason": "startup_waiting",
            "last_candidate_source": None,
            "last_source_age_s": None,
            "last_station_distance_m": None,
            "station_available": False,
            "source_timestamp_available": False,
            "rejected_candidate_count": 0,
            "departure_started_at_epoch": None,
            "observations": {},
        }
        owner._position_trust_state = state  # noqa: SLF001
    return state


def _observation_marker(
    source: str,
    candidate: dict[str, float | None] | None,
    source_time: Any,
    arrival_marker: Any,
) -> tuple[Any, ...] | None:
    if candidate is None:
        return None
    source_epoch = _source_epoch(source_time)
    return (
        source,
        round(float(candidate["x"]), 4),
        round(float(candidate["y"]), 4),
        round(float(candidate["heading"]), 4) if candidate.get("heading") is not None else None,
        round(source_epoch, 3) if source_epoch is not None else None,
        round(float(arrival_marker), 3) if _as_float(arrival_marker) is not None else None,
    )


def _observe(
    state: dict[str, Any],
    source: str,
    candidate: dict[str, float | None] | None,
    source_time: Any,
    arrival_marker: Any,
) -> tuple[int, bool]:
    marker = _observation_marker(source, candidate, source_time, arrival_marker)
    if marker is None:
        return 0, False
    rows = state.setdefault("observations", {})
    row = rows.get(source)
    if not isinstance(row, dict):
        row = {"marker": None, "count": 0}
        rows[source] = row
    if row.get("marker") == marker:
        return int(row.get("count") or 0), False
    row["marker"] = marker
    row["count"] = int(row.get("count") or 0) + 1
    return int(row["count"]), True


def _unavailable_override(source: str) -> dict[str, Any]:
    return {
        "value": None,
        "source": source,
        "source_age_s": None,
        "stale": True,
        "available": False,
    }


def _held_override(position: dict[str, float | None]) -> dict[str, Any]:
    return {
        "value": dict(position),
        "source": "trusted_previous",
        "source_age_s": None,
        "stale": False,
        "available": True,
    }


def _note_candidate(
    state: dict[str, Any],
    *,
    source: str,
    source_time: Any,
    station_distance_m: float | None,
    now_epoch: float,
) -> None:
    age = _source_age_s(source_time, now_epoch)
    state["last_candidate_source"] = source
    state["last_source_age_s"] = round(age, 3) if age is not None else None
    state["last_station_distance_m"] = (
        round(station_distance_m, 3) if station_distance_m is not None else None
    )
    state["source_timestamp_available"] = _source_epoch(source_time) is not None


def _accept(
    state: dict[str, Any],
    *,
    source: str,
    reason: str,
    end_wait: bool = True,
) -> None:
    if state.get("first_trusted_source") is None:
        state["first_trusted_source"] = source
    if end_wait:
        state["phase"] = "ready"
        state["ready"] = True
        state["departure_started_at_epoch"] = None
    state["last_reason"] = reason


def _reject(state: dict[str, Any], *, reason: str, is_new: bool) -> None:
    state["last_reason"] = reason
    if is_new:
        state["rejected_candidate_count"] = int(state.get("rejected_candidate_count") or 0) + 1


def _candidate_startup_ok(
    state: dict[str, Any],
    *,
    candidate: dict[str, float | None] | None,
    source: str,
    source_time: Any,
    arrival_marker: Any,
    docked: bool,
    station: dict[str, Any] | None,
    now_epoch: float,
    now_mono: float,
) -> tuple[bool, str]:
    if candidate is None:
        return False, "missing_candidate"

    observation_count, is_new = _observe(
        state, source, candidate, source_time, arrival_marker
    )
    station_distance = _distance(candidate, station)
    _note_candidate(
        state,
        source=source,
        source_time=source_time,
        station_distance_m=station_distance,
        now_epoch=now_epoch,
    )

    if docked and station is not None:
        if station_distance is not None and station_distance <= DOCK_STATION_TOLERANCE_M:
            return True, "docked_station_consistent"
        _reject(state, reason="docked_station_inconsistent", is_new=is_new)
        return False, "docked_station_inconsistent"

    source_age = _source_age_s(source_time, now_epoch)
    if source_age is not None and source_age <= SOURCE_FRESH_MAX_AGE_S:
        return True, "fresh_vendor_timestamp"

    if _source_epoch(source_time) is None and observation_count >= 2:
        return True, "timestamp_missing_repeated_pose"

    started = _as_float(state.get("started_at_mono"))
    elapsed = max(0.0, now_mono - started) if started is not None else 0.0
    if station is None and elapsed >= STARTUP_FAIL_OPEN_S:
        return True, "startup_timeout_actual_pose"

    _reject(state, reason="startup_pose_not_trusted", is_new=is_new)
    return False, "startup_pose_not_trusted"


def _candidate_departure_ok(
    state: dict[str, Any],
    *,
    candidate: dict[str, float | None] | None,
    source: str,
    source_time: Any,
    arrival_marker: Any,
    station: dict[str, Any] | None,
    now_epoch: float,
) -> tuple[str, str]:
    """Return one of accepted/release, accepted/hold-gate or rejected."""
    if candidate is None:
        return "rejected", "missing_candidate"

    _count, is_new = _observe(state, source, candidate, source_time, arrival_marker)
    station_distance = _distance(candidate, station)
    _note_candidate(
        state,
        source=source,
        source_time=source_time,
        station_distance_m=station_distance,
        now_epoch=now_epoch,
    )

    source_age = _source_age_s(source_time, now_epoch)
    source_epoch = _source_epoch(source_time) if source_age is not None else None
    departure_epoch = _as_float(state.get("departure_started_at_epoch"))
    if (
        source_epoch is not None
        and departure_epoch is not None
        and source_epoch >= departure_epoch - DEPARTURE_SOURCE_SKEW_S
    ):
        return "release", "post_departure_vendor_pose"

    # A real pose still at the dock is safe to show, but it does not prove that
    # a later far-away cached pose belongs to the newly started trip.
    if station is not None and station_distance is not None and station_distance <= DOCK_STATION_TOLERANCE_M:
        return "hold_gate", "departure_pose_still_at_station"

    _reject(state, reason="departure_pose_precedes_transition", is_new=is_new)
    return "rejected", "departure_pose_precedes_transition"


def prepare_position_candidates(
    owner: Any,
    snapshot: dict[str, Any],
    *,
    mqtt_position: Any,
    cloud_position: Any,
    mqtt_pose_time: Any,
    cloud_report_time: Any,
    now_epoch: float | None = None,
    now_mono: float | None = None,
) -> dict[str, Any]:
    """Filter real pose candidates before Canonical PositionResolver publishes them."""
    epoch = time.time() if now_epoch is None else float(now_epoch)
    mono = time.monotonic() if now_mono is None else float(now_mono)
    state = _state(owner, now_epoch=epoch, now_mono=mono)

    mqtt = _position(mqtt_position)
    cloud = _position(cloud_position)
    station = _station_position(owner, snapshot)
    docked = snapshot.get("docked") is True
    state["station_available"] = station is not None

    previous_docked = state.get("last_docked")
    if state.get("ready") and previous_docked is True and not docked:
        state["phase"] = "departure_waiting"
        state["departure_started_at_epoch"] = epoch
        state["last_reason"] = "departure_waiting_for_current_pose"

    if state.get("phase") == "departure_waiting" and docked:
        state["phase"] = "ready"
        state["departure_started_at_epoch"] = None
        state["last_reason"] = "departure_cancelled_docked"

    mqtt_arrival = getattr(owner, "_mqtt_last_update", None)

    if state.get("phase") == "startup_waiting":
        mqtt_ok, mqtt_reason = _candidate_startup_ok(
            state,
            candidate=mqtt,
            source="official_mqtt",
            source_time=mqtt_pose_time,
            arrival_marker=mqtt_arrival,
            docked=docked,
            station=station,
            now_epoch=epoch,
            now_mono=mono,
        )
        cloud_ok, cloud_reason = _candidate_startup_ok(
            state,
            candidate=cloud,
            source="private_cloud",
            source_time=cloud_report_time,
            arrival_marker=None,
            docked=docked,
            station=station,
            now_epoch=epoch,
            now_mono=mono,
        )

        if mqtt_ok:
            _accept(state, source="official_mqtt", reason=mqtt_reason)
            return {"mqtt_position": mqtt, "cloud_position": cloud, "position_override": None}
        if cloud_ok:
            _accept(state, source="private_cloud", reason=cloud_reason)
            return {"mqtt_position": None, "cloud_position": cloud, "position_override": None}
        state["last_reason"] = (
            "startup_waiting_for_station_consistent_pose"
            if docked and station is not None
            else "startup_waiting_for_current_pose"
        )
        return {
            "mqtt_position": None,
            "cloud_position": None,
            "position_override": _unavailable_override("startup_waiting"),
        }

    if state.get("phase") == "departure_waiting":
        mqtt_decision, mqtt_reason = _candidate_departure_ok(
            state,
            candidate=mqtt,
            source="official_mqtt",
            source_time=mqtt_pose_time,
            arrival_marker=mqtt_arrival,
            station=station,
            now_epoch=epoch,
        )
        cloud_decision, cloud_reason = _candidate_departure_ok(
            state,
            candidate=cloud,
            source="private_cloud",
            source_time=cloud_report_time,
            arrival_marker=None,
            station=station,
            now_epoch=epoch,
        )

        if mqtt_decision == "release":
            _accept(state, source="official_mqtt", reason=mqtt_reason)
            return {"mqtt_position": mqtt, "cloud_position": cloud, "position_override": None}
        if cloud_decision == "release":
            _accept(state, source="private_cloud", reason=cloud_reason)
            return {"mqtt_position": None, "cloud_position": cloud, "position_override": None}

        # Safe near-station candidates may update heading/XY without ending the
        # gate. A later old far-away pose will still be blocked.
        if mqtt_decision == "hold_gate":
            _accept(state, source="official_mqtt", reason=mqtt_reason, end_wait=False)
            return {"mqtt_position": mqtt, "cloud_position": None, "position_override": None}
        if cloud_decision == "hold_gate":
            _accept(state, source="private_cloud", reason=cloud_reason, end_wait=False)
            return {"mqtt_position": None, "cloud_position": cloud, "position_override": None}

        previous = _position(state.get("last_position"))
        if previous is not None:
            state["last_reason"] = "departure_holding_last_trusted_pose"
            return {
                "mqtt_position": None,
                "cloud_position": None,
                "position_override": _held_override(previous),
            }
        return {
            "mqtt_position": None,
            "cloud_position": None,
            "position_override": _unavailable_override("departure_waiting"),
        }

    return {"mqtt_position": mqtt, "cloud_position": cloud, "position_override": None}


def record_position_result(
    owner: Any,
    snapshot: dict[str, Any],
    canonical_state: dict[str, Any],
) -> None:
    """Remember only the last already-published real/held pose for transition guarding."""
    state = getattr(owner, "_position_trust_state", None)
    if not isinstance(state, dict):
        return
    navigation = canonical_state.get("navigation") if isinstance(canonical_state.get("navigation"), dict) else {}
    position = navigation.get("position") if isinstance(navigation.get("position"), dict) else {}
    value = _position(position.get("value"))
    if value is not None:
        state["last_position"] = value
    state["last_docked"] = snapshot.get("docked") is True


def position_trust_diagnostics(owner: Any) -> dict[str, Any] | None:
    """Return privacy-safe gate state without exact mower/station coordinates."""
    state = getattr(owner, "_position_trust_state", None)
    if not isinstance(state, dict):
        return None
    return {
        "schema_version": state.get("schema_version"),
        "phase": state.get("phase"),
        "ready": bool(state.get("ready")),
        "last_reason": state.get("last_reason"),
        "first_trusted_source": state.get("first_trusted_source"),
        "last_candidate_source": state.get("last_candidate_source"),
        "last_source_age_s": state.get("last_source_age_s"),
        "source_timestamp_available": bool(state.get("source_timestamp_available")),
        "station_available": bool(state.get("station_available")),
        "station_distance_m": state.get("last_station_distance_m"),
        "station_tolerance_m": DOCK_STATION_TOLERANCE_M,
        "rejected_candidate_count": int(state.get("rejected_candidate_count") or 0),
        "departure_waiting": state.get("phase") == "departure_waiting",
        "coordinates_included": False,
    }
