"""Authoritative navigation target freshness and gate-intent arbitration.

This module is the single post-fallback owner of navigation intent. It keeps
field freshness separate from MQTT connection/pose freshness and protects an
in-zone mowing command from an older opposite-zone target without clearing a
gate transition when position evidence is stale or the mower is already inside
a mapped channel.
"""
from __future__ import annotations

import math
import time
from typing import Any

from . import coordinator as _coordinator
from . import mqtt as _mqtt
from . import navigation_fallback as _fallback
from .const import MQTT_STATE_STALE_SECONDS


_SAME_ZONE_COMMAND_GUARD_SECONDS = 120.0
_VENDOR_TARGET_SOURCES = {
    "mqtt_work_target",
    "mqtt_partition_ids",
    "private_current_zones",
    "private_work_target",
}
_NON_AUTHORITATIVE_TARGET_SOURCES = {"last_known", "gate_arrival_guard"}
_INVALID_PHYSICAL_STATES = {
    "Between zones",
    "Outside mapped zones",
    "Position unavailable",
}


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _single_zone(values: Any) -> int | None:
    parsed: list[int] = []
    for raw in values or []:
        zone_id = _as_int(raw)
        if zone_id is not None and zone_id > 0 and zone_id not in parsed:
            parsed.append(zone_id)
    return parsed[0] if len(parsed) == 1 else None


def _age_seconds(stamp: Any, now: float | None = None) -> float | None:
    value = _as_float(stamp)
    current = _as_float(time.monotonic() if now is None else now)
    if value is None or current is None:
        return None
    age = current - value
    if age < 0:
        return None
    return age


def _field_is_fresh(
    stamp: Any,
    *,
    now: float | None = None,
    max_age: float = float(MQTT_STATE_STALE_SECONDS),
) -> bool:
    age = _age_seconds(stamp, now)
    return age is not None and age <= max(0.0, float(max_age))


def _report_seconds(value: Any) -> float | None:
    parsed = _as_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed / 1000.0 if parsed > 10_000_000_000 else parsed


def _advance_confirmation(
    previous: dict[str, Any] | None,
    *,
    zone_id: int,
    report_seconds: float,
) -> dict[str, Any]:
    """Advance only on a strictly newer vendor report for the same zone."""
    prior = previous if isinstance(previous, dict) else {}
    prior_zone = _as_int(prior.get("zone"))
    prior_report = _report_seconds(prior.get("report_seconds") or prior.get("report"))
    count = max(0, _as_int(prior.get("count")) or 0)

    retained_report = report_seconds
    if prior_zone != zone_id:
        count = 1
    elif prior_report is None:
        count = max(1, count)
    elif report_seconds > prior_report:
        count += 1
    else:
        # Duplicate and out-of-order reports preserve both count and the newest
        # accepted timestamp, so a later still-old packet cannot advance it.
        retained_report = prior_report
    return {
        "zone": zone_id,
        "report": retained_report,
        "report_seconds": retained_report,
        "count": count,
    }


def _latch_conflicts_with_zone(latch: Any, zone_id: Any) -> bool:
    if not isinstance(latch, dict):
        return False
    zone = _as_int(zone_id)
    from_id = _as_int(latch.get("from_zone_id"))
    to_id = _as_int(latch.get("to_zone_id"))
    return bool(
        zone is not None
        and from_id == zone
        and to_id is not None
        and to_id != zone
        and latch.get("release_at") is None
    )


def _mapped_channel_active(data: dict[str, Any], latch: Any = None) -> bool:
    channel_id = _as_int(data.get("current_channel_id"))
    if channel_id is None:
        return False
    if data.get("current_channel_stale") is True:
        return False
    connection = {
        value
        for value in (
            _as_int(item) for item in data.get("current_channel_connection") or []
        )
        if value is not None
    }
    if not isinstance(latch, dict):
        return bool(connection)
    pair = {
        value
        for value in (
            _as_int(latch.get("from_zone_id")),
            _as_int(latch.get("to_zone_id")),
        )
        if value is not None
    }
    return bool(pair and connection == pair)


def _clear_conflicting_latches(
    coordinator: Any,
    zone_id: int,
    *,
    navigation: dict[str, Any] | None = None,
) -> list[str]:
    """Clear only pre-transit latches contradicted by fresh in-zone evidence."""
    context = navigation or coordinator.data or {}
    removed: list[str] = []
    for slug, latch in list(coordinator._gate_latches.items()):  # noqa: SLF001
        if not _latch_conflicts_with_zone(latch, zone_id):
            continue
        if _mapped_channel_active(context, latch):
            continue
        coordinator._gate_latches.pop(slug, None)  # noqa: SLF001
        coordinator._cancel_gate_release(slug)  # noqa: SLF001
        removed.append(str(slug))
    return removed


def _clear_gate_state_rows(
    result: dict[str, Any],
    slugs: list[str],
    *,
    zone_id: int,
    reason: str,
) -> None:
    states = result.get("gate_states") or {}
    for slug in slugs:
        state = states.get(slug)
        if not isinstance(state, dict):
            continue
        state.update(
            {
                "required": False,
                "close_delay_remaining": None,
                "from_zone_id": None,
                "from_zone_name": None,
                "to_zone_id": None,
                "to_zone_name": None,
                "target_zone_id": zone_id,
                "target_source": result.get("target_zone_source"),
                "stale_intent_cancelled": True,
                "stale_intent_cancel_reason": reason,
            }
        )


def _no_gate_required(result: dict[str, Any]) -> bool:
    return not any(
        isinstance(state, dict) and state.get("required") is True
        for state in (result.get("gate_states") or {}).values()
    )


def _physical_zone_is_fresh(data: dict[str, Any], zone_id: int) -> bool:
    if _as_int(data.get("current_physical_zone_id")) != zone_id:
        return False
    if data.get("current_physical_zone_stale") is not False:
        return False
    return str(data.get("current_physical_zone_position_source") or "") in {
        "mqtt",
        "private_cloud",
    }


def _target_freshness(coordinator: Any, now: float) -> dict[str, Any]:
    work_stamp = getattr(coordinator, "_mqtt_work_target_last_update", None)
    partition_stamp = getattr(coordinator, "_mqtt_partition_ids_last_update", None)
    work_age = _age_seconds(work_stamp, now)
    partition_age = _age_seconds(partition_stamp, now)
    return {
        "work_target_age": work_age,
        "partition_ids_age": partition_age,
        "work_target_fresh": _field_is_fresh(work_stamp, now=now),
        "partition_ids_fresh": _field_is_fresh(partition_stamp, now=now),
    }


def _strict_cloud_gate_transition(
    coordinator: Any,
    snapshot: dict[str, Any],
    context: dict[str, Any],
) -> bool:
    """Require two strictly newer cloud samples before clearing a gate latch."""
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

    physical_id = _as_int((physical or {}).get("id"))
    report_seconds = _report_seconds(_fallback._cloud_report_time(snapshot))  # noqa: SLF001
    confirmations = getattr(coordinator, "_cloud_gate_confirmations", {})

    blocked = False
    for slug, latch in list(coordinator._gate_latches.items()):  # noqa: SLF001
        from_id = _as_int((latch or {}).get("from_zone_id"))
        to_id = _as_int((latch or {}).get("to_zone_id"))
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
        if report_seconds is None:
            blocked = True
            continue
        row = _advance_confirmation(
            confirmations.get(slug),
            zone_id=int(physical_id),
            report_seconds=report_seconds,
        )
        confirmations[slug] = row
        if int(row["count"]) < 2:
            blocked = True

    coordinator._cloud_gate_confirmations = confirmations  # noqa: SLF001
    return blocked


def install_navigation_intent() -> None:
    """Install one post-fallback target/gate arbitration layer."""
    cls = _coordinator.NavimowCoordinator
    if getattr(cls, "_navigation_intent_installed", False):
        return

    original_parse_location = _mqtt.parse_location_payload
    original_ingest = cls.ingest_mqtt_location
    original_set_target = cls.set_command_target
    original_clear_target = cls.clear_command_target
    original_navigation = cls._navigation_fields
    original_channel_state = cls.channel_state

    def parse_location_payload(
        cache: dict[str, dict[str, Any]],
        device_id: str,
        data: Any,
    ) -> dict[str, Any] | None:
        result = original_parse_location(cache, device_id, data)
        if result is None:
            return None
        items = data if isinstance(data, list) else []
        result["_work_target_updated"] = bool(
            result.get("_work_progress_updated") is True
            and any(
                isinstance(item, dict)
                and item.get("type") == 2
                and "mapWorkPosition" in item
                for item in items
            )
        )
        result["_partition_ids_updated"] = any(
            isinstance(item, dict)
            and item.get("type") == 3
            and "partitionIds" in item
            for item in items
        )
        return result

    def ingest_mqtt_location(self: Any, location: dict[str, Any]) -> None:
        if isinstance(location, dict):
            now = time.monotonic()
            previous = self._mqtt_location or {}  # noqa: SLF001
            if (
                location.get("_work_target_updated") is True
                or (
                    "work_target_zone" in location
                    and location.get("work_target_zone")
                    != previous.get("work_target_zone")
                )
            ):
                self._mqtt_work_target_last_update = now  # noqa: SLF001
            if (
                location.get("_partition_ids_updated") is True
                or (
                    "partition_ids" in location
                    and location.get("partition_ids")
                    != previous.get("partition_ids")
                )
            ):
                self._mqtt_partition_ids_last_update = now  # noqa: SLF001
        return original_ingest(self, location)

    def set_command_target(
        self: Any,
        zone_ids: list[int],
        *,
        source: str = "ha_mow_command",
    ) -> None:
        ids = _coordinator._dedupe_zone_ids(zone_ids)  # noqa: SLF001
        command_zone = _single_zone(ids)
        data = self.data or {}
        if (
            command_zone is not None
            and _physical_zone_is_fresh(data, command_zone)
            and not _mapped_channel_active(data)
        ):
            removed = _clear_conflicting_latches(
                self,
                command_zone,
                navigation=data,
            )
            self._same_zone_command_gate_guard = {  # noqa: SLF001
                "zone_id": command_zone,
                "started_at": time.monotonic(),
                "source": str(source),
                "cleared_latches": removed,
            }
        else:
            self._same_zone_command_gate_guard = None  # noqa: SLF001
        original_set_target(self, zone_ids, source=source)

    def clear_command_target(self: Any) -> None:
        original_clear_target(self)
        if not getattr(self, "_navigation_intent_resolving", False):
            self._same_zone_command_gate_guard = None  # noqa: SLF001

    def channel_state(self: Any, channel: Any) -> bool | None:
        # MQTT and non-usable cloud paths retain the existing implementation.
        if self._fresh_mqtt_position() is not None:  # noqa: SLF001
            return original_channel_state(self, channel)
        data = self.data or {}
        context = _fallback._position_context(self, data)  # noqa: SLF001
        if context.get("source") != "private_cloud" or not context.get("gate_usable"):
            return original_channel_state(self, channel)

        report_seconds = _report_seconds(_fallback._cloud_report_time(data))  # noqa: SLF001
        states = getattr(self, "_cloud_gate_area_states", {})
        previous = states.get(channel.slug) or {}
        previous_report = _report_seconds(
            previous.get("report_seconds") or previous.get("report")
        )
        if (
            report_seconds is None
            or (
                previous_report is not None
                and report_seconds <= previous_report
            )
        ):
            value = previous.get("value")
            return value if isinstance(value, bool) else None

        value = original_channel_state(self, channel)
        row = (getattr(self, "_cloud_gate_area_states", {}) or {}).get(channel.slug)
        if isinstance(row, dict):
            row["report_seconds"] = report_seconds
        return value

    def navigation_fields(
        self: Any,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        now = time.monotonic()
        freshness = _target_freshness(self, now)
        original_location = self._mqtt_location  # noqa: SLF001
        sanitized = dict(original_location or {})
        stale_fields: list[str] = []
        if "work_target_zone" in sanitized and not freshness["work_target_fresh"]:
            sanitized.pop("work_target_zone", None)
            stale_fields.append("work_target_zone")
        if "partition_ids" in sanitized and not freshness["partition_ids_fresh"]:
            sanitized.pop("partition_ids", None)
            sanitized.pop("partition", None)
            stale_fields.append("partition_ids")

        self._mqtt_location = sanitized  # noqa: SLF001
        self._navigation_intent_resolving = True  # noqa: SLF001
        try:
            result = original_navigation(self, snapshot)
        finally:
            self._navigation_intent_resolving = False  # noqa: SLF001
            self._mqtt_location = original_location  # noqa: SLF001

        result["mqtt_navigation_target_age"] = {
            "work_target_zone": freshness["work_target_age"],
            "partition_ids": freshness["partition_ids_age"],
        }
        result["mqtt_navigation_target_fresh"] = {
            "work_target_zone": freshness["work_target_fresh"],
            "partition_ids": freshness["partition_ids_fresh"],
        }
        result["mqtt_navigation_target_stale_fields"] = stale_fields

        guard = getattr(self, "_same_zone_command_gate_guard", None)
        if not isinstance(guard, dict):
            return result

        zone_id = _as_int(guard.get("zone_id"))
        age = _age_seconds(guard.get("started_at"), now)
        if (
            zone_id is None
            or age is None
            or age > _SAME_ZONE_COMMAND_GUARD_SECONDS
        ):
            self._same_zone_command_gate_guard = None  # noqa: SLF001
            return result

        # Unknown/stale position is never evidence that a transition ended.
        if not _physical_zone_is_fresh(result, zone_id):
            self._same_zone_command_gate_guard = None  # noqa: SLF001
            return result
        if _mapped_channel_active(result):
            self._same_zone_command_gate_guard = None  # noqa: SLF001
            return result

        target_ids = _coordinator._dedupe_zone_ids(result.get("target_zone_ids"))  # noqa: SLF001
        target_source = str(result.get("target_zone_source") or "")
        if target_source == "returning_to_dock":
            self._same_zone_command_gate_guard = None  # noqa: SLF001
            return result

        # A fresh explicit same-zone command owns the hand-over window. A
        # last-known or vendor target that contradicts it is retained in
        # diagnostics but cannot create a pre-transit gate latch.
        if (
            target_ids
            and target_ids != [zone_id]
            and (
                target_source in _VENDOR_TARGET_SOURCES
                or target_source in _NON_AUTHORITATIVE_TARGET_SOURCES
            )
        ):
            removed = _clear_conflicting_latches(
                self,
                zone_id,
                navigation=result,
            )
            self._last_target_zone_ids = [zone_id]  # noqa: SLF001
            stale_target_ids = list(target_ids)
            result["target_zone_ids"] = [zone_id]
            current_name = str(result.get("current_physical_zone") or "").strip()
            if current_name and current_name not in _INVALID_PHYSICAL_STATES:
                result["target_zone"] = current_name
            result["target_zone_source"] = "same_zone_command_guard"
            result["same_zone_command_guard"] = {
                "active": True,
                "zone_id": zone_id,
                "age_seconds": round(age, 1),
                "command_source": guard.get("source"),
                "suppressed_target_zone_ids": stale_target_ids,
                "suppressed_target_source": target_source,
            }
            _clear_gate_state_rows(
                result,
                removed,
                zone_id=zone_id,
                reason="stale_target_after_fresh_same_zone_command",
            )
            if _no_gate_required(result):
                result["zone_transition"] = False
            return result

        # Same-zone last-known state is not a vendor acknowledgement. Keep the
        # bounded guard alive, but expose that no suppression was needed.
        if target_ids == [zone_id] and target_source in _NON_AUTHORITATIVE_TARGET_SOURCES:
            result["same_zone_command_guard"] = {
                "active": True,
                "zone_id": zone_id,
                "age_seconds": round(age, 1),
                "command_source": guard.get("source"),
                "awaiting_fresh_confirmation": True,
            }
        return result

    _mqtt.parse_location_payload = parse_location_payload
    _fallback._risky_cloud_gate_transition = _strict_cloud_gate_transition  # noqa: SLF001
    cls.ingest_mqtt_location = ingest_mqtt_location
    cls.set_command_target = set_command_target
    cls.clear_command_target = clear_command_target
    cls.channel_state = channel_state
    cls._navigation_fields = navigation_fields
    cls._navigation_intent_installed = True
