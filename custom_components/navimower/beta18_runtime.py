"""Beta18 navigation fallback corrections.

MQTT remains the primary source for physical navigation. When a fresh MQTT pose
is unavailable, the private-cloud pose can keep physical-zone/channel display
useful. Gate decisions may use private-cloud X/Y only while the vendor's own
``report_time`` is recent; stale cloud coordinates are display-only.

For closing/clearing an already-open gate, a private-cloud transition away from
the origin must be confirmed by two distinct vendor pose timestamps. This keeps
one delayed cloud sample from closing a physical gate in front of the mower.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from . import coordinator as _coordinator
from .position_fallback import choose_position


class _NavigationProxy:
    """Forward coordinator state while overriding the pose used by navigation."""

    def __init__(self, target: Any, position: dict[str, Any] | None, age: float | None) -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_position", position)
        object.__setattr__(self, "_age", age)

    def __getattr__(self, name: str) -> Any:
        if name == "_fresh_mqtt_position":
            return lambda: object.__getattribute__(self, "_position")
        if name == "pose_age":
            return lambda: object.__getattribute__(self, "_age")
        return getattr(object.__getattribute__(self, "_target"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_target"), name, value)


def _cloud_report_time(snapshot: dict[str, Any]) -> Any:
    raw = snapshot.get("raw") or {}
    location = raw.get("location") or {}
    if isinstance(location, dict) and location.get("report_time") is not None:
        return location.get("report_time")
    if snapshot.get("pose_source") == "private_cloud":
        return snapshot.get("pose_time")
    return None


def _position_context(coordinator: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    return choose_position(
        mqtt_position=coordinator._fresh_mqtt_position(),  # noqa: SLF001
        mqtt_age=coordinator.pose_age(),
        cloud_position=(
            snapshot.get("cloud_position")
            if isinstance(snapshot.get("cloud_position"), dict)
            else (
                snapshot.get("position")
                if snapshot.get("pose_source") == "private_cloud"
                and isinstance(snapshot.get("position"), dict)
                else None
            )
        ),
        cloud_report_time=_cloud_report_time(snapshot),
    )


def _risky_cloud_gate_transition(coordinator: Any, snapshot: dict[str, Any], context: dict[str, Any]) -> bool:
    """Require two cloud samples before an existing gate may close/clear."""
    if context.get("source") != "private_cloud" or not context.get("gate_usable"):
        return False
    if not coordinator._gate_latches:  # noqa: SLF001
        return False

    map_data = snapshot.get("map") or {}
    zones = map_data.get("zones") or snapshot.get("zones") or []
    channels = map_data.get("channels") or []
    position = context.get("position")
    physical = _coordinator._zone_at_position(position, zones)  # noqa: SLF001
    tunnel = None
    if physical is None:
        tunnel = _coordinator._tunnel_at_position(position, channels)  # noqa: SLF001
    if tunnel is not None:
        return False

    physical_id = _coordinator._as_int((physical or {}).get("id"))  # noqa: SLF001
    report_key = str(_cloud_report_time(snapshot) or "")
    confirmations = getattr(coordinator, "_beta18_cloud_gate_confirmations", {})

    blocked = False
    for slug, latch in list(coordinator._gate_latches.items()):  # noqa: SLF001
        from_id = _coordinator._as_int((latch or {}).get("from_zone_id"))  # noqa: SLF001
        to_id = _coordinator._as_int((latch or {}).get("to_zone_id"))  # noqa: SLF001
        pair = {value for value in (from_id, to_id) if value is not None}
        risky = bool(
            physical_id is not None
            and (
                (to_id is not None and physical_id == to_id)
                or (pair and physical_id not in pair)
            )
        )
        if not risky:
            confirmations.pop(slug, None)
            continue

        key = f"{physical_id}:{report_key}"
        previous = confirmations.get(slug) or {}
        count = int(previous.get("count") or 0)
        previous_report = str(previous.get("report") or "")
        previous_zone = previous.get("zone")
        if report_key and report_key != previous_report and previous_zone == physical_id:
            count += 1
        else:
            count = 1
        confirmations[slug] = {
            "zone": physical_id,
            "report": report_key,
            "key": key,
            "count": count,
        }
        if count < 2:
            blocked = True

    coordinator._beta18_cloud_gate_confirmations = confirmations  # noqa: SLF001
    return blocked


def _decorate_navigation_result(
    coordinator: Any,
    snapshot: dict[str, Any],
    result: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    source = str(context.get("source") or "unavailable")
    age = context.get("age")
    stale = bool(context.get("stale"))
    position = context.get("position")

    # If cloud is too old for gate logic, still use it for display-only zone and
    # channel identification. The gate result was already calculated without it.
    if source == "private_cloud" and position is not None and not context.get("gate_usable"):
        map_data = snapshot.get("map") or {}
        zones = map_data.get("zones") or snapshot.get("zones") or []
        channels = map_data.get("channels") or []
        physical = _coordinator._zone_at_position(position, zones)  # noqa: SLF001
        tunnel = None
        if physical is None:
            tunnel = _coordinator._tunnel_at_position(position, channels)  # noqa: SLF001
        physical_id = _coordinator._as_int((physical or {}).get("id"))  # noqa: SLF001
        physical_name = (physical or {}).get("name")
        if physical_id is not None and physical_name:
            result["current_physical_zone"] = str(physical_name)
            result["current_physical_zone_id"] = physical_id
            coordinator._last_physical_zone_id = physical_id  # noqa: SLF001
            coordinator._last_physical_zone_name = str(physical_name)  # noqa: SLF001
        elif tunnel is not None:
            result["current_physical_zone"] = "Between zones"
            result["current_physical_zone_id"] = None
        else:
            result["current_physical_zone"] = "Outside mapped zones"
            result["current_physical_zone_id"] = None

        if tunnel is not None:
            result["current_channel"] = str(
                tunnel.get("name")
                or (f"Channel {tunnel.get('id')}" if tunnel.get("id") is not None else "Channel")
            )
            result["current_channel_id"] = _coordinator._as_int(tunnel.get("id"))  # noqa: SLF001
            result["current_channel_connection"] = _coordinator._dedupe_zone_ids(  # noqa: SLF001
                tunnel.get("connection")
            )
            result["current_channel_distance"] = _coordinator._as_float(tunnel.get("distance"))  # noqa: SLF001
        else:
            result["current_channel"] = "Not in channel"
            result["current_channel_id"] = None
            result["current_channel_connection"] = []
            result["current_channel_distance"] = None

    if source == "mqtt":
        position_source = "mqtt"
    elif source == "private_cloud":
        position_source = "private_cloud"
    elif result.get("current_physical_zone_source") == "last_known_stale_pose":
        position_source = "last_known"
        stale = True
    else:
        position_source = "unavailable"
        stale = True

    if result.get("current_physical_zone_id") is not None:
        result["current_physical_zone_source"] = (
            "mqtt_map_polygon" if position_source == "mqtt" else
            "private_cloud_map_polygon" if position_source == "private_cloud" else
            result.get("current_physical_zone_source")
        )
    result["current_physical_zone_position_source"] = position_source
    result["current_physical_zone_position_age"] = age
    result["current_physical_zone_stale"] = stale

    if position_source == "private_cloud":
        result["current_channel_source"] = "private_cloud_pose"
        result["current_channel_stale"] = stale
        result["current_channel_pose_valid"] = bool(context.get("gate_usable"))
        result["current_channel_pose_age"] = age
    elif position_source == "mqtt":
        result["current_channel_pose_age"] = coordinator.pose_age()

    for state in (result.get("gate_states") or {}).values():
        if not isinstance(state, dict):
            continue
        state["position_source"] = position_source
        state["position_age"] = age
        state["position_stale"] = stale
        state["mqtt_pose_valid"] = coordinator._fresh_mqtt_position() is not None  # noqa: SLF001
        state["cloud_fallback"] = position_source == "private_cloud"

    return result


def _install_sensor_attributes() -> None:
    from . import sensor as platform

    def physical_attrs(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "zone_id": data.get("current_physical_zone_id"),
            "source": data.get("current_physical_zone_source"),
            "position_source": data.get("current_physical_zone_position_source"),
            "position_age": data.get("current_physical_zone_position_age"),
            "stale": data.get("current_physical_zone_stale"),
            "mqtt_pose_valid": data.get("mqtt_pose_valid"),
        }

    platform.SENSORS = tuple(
        replace(description, attrs_fn=physical_attrs)
        if description.key == "current_physical_zone"
        else description
        for description in platform.SENSORS
    )


def install_beta18_runtime() -> None:
    """Install freshness-aware navigation fallback once per interpreter."""
    cls = _coordinator.NavimowCoordinator
    if getattr(cls, "_beta18_runtime_installed", False):
        return

    original_navigation = cls._navigation_fields
    original_channel_state = cls.channel_state

    def navigation_fields(self: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
        context = _position_context(self, snapshot)
        gate_position = context.get("position") if context.get("gate_usable") else None
        gate_age = context.get("age") if gate_position is not None else None
        if gate_position is not None and _risky_cloud_gate_transition(self, snapshot, context):
            gate_position = None
            gate_age = None
        proxy = _NavigationProxy(self, gate_position, gate_age)
        result = original_navigation(proxy, snapshot)
        return _decorate_navigation_result(self, snapshot, result, context)

    def channel_state(self: Any, channel: Any) -> bool | None:
        mqtt_position = self._fresh_mqtt_position()  # noqa: SLF001
        if mqtt_position is not None:
            states = getattr(self, "_beta18_gate_area_states", {})
            states[channel.slug] = {
                "value": channel.contains(mqtt_position.get("x"), mqtt_position.get("y")),
                "report": None,
                "outside_count": 0,
            }
            self._beta18_gate_area_states = states
            return original_channel_state(self, channel)

        data = self.data or {}
        if data.get("docked") is True and self._pending_activity_value() is None:  # noqa: SLF001
            return False
        context = _position_context(self, data)
        if context.get("source") != "private_cloud" or not context.get("gate_usable"):
            return None
        position = context.get("position") or {}
        value = channel.contains(position.get("x"), position.get("y"))
        if value is None:
            return None

        states = getattr(self, "_beta18_gate_area_states", {})
        previous = states.get(channel.slug) or {}
        report = str(_cloud_report_time(data) or "")
        if value:
            states[channel.slug] = {"value": True, "report": report, "outside_count": 0}
            self._beta18_gate_area_states = states
            return True

        outside_count = int(previous.get("outside_count") or 0)
        if report and report != str(previous.get("report") or ""):
            outside_count += 1
        else:
            outside_count = max(1, outside_count)
        states[channel.slug] = {
            "value": previous.get("value"),
            "report": report,
            "outside_count": outside_count,
        }
        self._beta18_gate_area_states = states
        if outside_count >= 2:
            states[channel.slug]["value"] = False
            return False
        return True if previous.get("value") is True else None

    cls._navigation_fields = navigation_fields
    cls.channel_state = channel_state
    cls._beta18_runtime_installed = True
    _install_sensor_attributes()
