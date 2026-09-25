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


def _zone_ids(values: Any) -> list[int]:
    """Normalize positive zone IDs without inheriting retained target state."""
    parsed: list[int] = []
    for raw in values or []:
        zone_id = _as_int(raw)
        if zone_id is not None and zone_id > 0 and zone_id not in parsed:
            parsed.append(zone_id)
    return parsed


def _single_zone(values: Any) -> int | None:
    parsed = _zone_ids(values)
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


def _task_target_active(snapshot: dict[str, Any]) -> bool:
    """Return whether vendor state still represents an active mowing task."""
    if snapshot.get("docked") is True:
        return False
    state_code = str(snapshot.get("state_code") or "")
    if state_code in {"0202", "0258"}:
        # Map editing is exposed as paused activity but is not a mowing task.
        return False
    if state_code in {"0210", "0211", "0212"}:
        return True
    activity = str(snapshot.get("activity") or "").strip().lower()
    if activity in {"mowing", "paused"}:
        return True
    return _as_int(snapshot.get("mqtt_vehicle_state")) == 4


def _resolve_public_task_target(
    *,
    is_docked: bool,
    is_returning: bool,
    task_active: bool,
    command_target_ids: Any,
    command_target_fresh: bool,
    mqtt_partition_ids: Any,
    mqtt_partition_fresh: bool,
    cloud_zone_ids: Any,
    mqtt_work_target: Any,
    mqtt_work_target_fresh: bool,
    cloud_work_target: Any,
) -> tuple[list[int], str]:
    """Resolve only user-facing mowing-task intent, never gate route retention."""
    if is_docked:
        return [], "docked"
    if is_returning:
        return [], "returning_to_dock"

    command_ids = _zone_ids(command_target_ids)
    if command_target_fresh and command_ids:
        return command_ids, "ha_command"

    if not task_active:
        return [], "none"

    mqtt_ids = _zone_ids(mqtt_partition_ids)
    if mqtt_partition_fresh:
        if mqtt_ids:
            return mqtt_ids, "mqtt_partition_ids"
        # An explicitly fresh empty partition list is meaningful for Mow All.
        # Do not revive an older private-cloud selection in that case.
    else:
        cloud_ids = _zone_ids(cloud_zone_ids)
        if cloud_ids:
            return cloud_ids, "private_current_zones"

    mqtt_work = _as_int(mqtt_work_target)
    if mqtt_work_target_fresh and mqtt_work is not None and mqtt_work > 0:
        return [mqtt_work], "mqtt_work_target"

    cloud_work = _as_int(cloud_work_target)
    if cloud_work is not None and cloud_work > 0:
        return [cloud_work], "private_work_target"
    return [], "none"


def _resolve_immediate_target(
    *,
    is_docked: bool,
    is_returning: bool,
    task_active: bool,
    command_target_ids: Any,
    command_target_fresh: bool,
    planned_zone_ids: Any,
    mqtt_work_target: Any,
    mqtt_work_target_fresh: bool,
    mqtt_work_target_after_command: bool,
    cloud_work_target: Any,
) -> tuple[list[int], str]:
    """Resolve one automation-safe immediate mowing target.

    Multi-zone task selection is deliberately not treated as an immediate
    target. A fresh vendor work target may take over from a fresh HA command
    only after it was observed at or after that command, preventing a retained
    target from the previous task from winning during dispatch.
    """
    if is_docked:
        return [], "docked"
    if is_returning:
        return [], "returning_to_dock"
    if not task_active:
        return [], "none"

    command_ids = _zone_ids(command_target_ids)
    planned_ids = _zone_ids(planned_zone_ids)
    mqtt_work = _as_int(mqtt_work_target)

    if (
        mqtt_work_target_fresh
        and mqtt_work is not None
        and mqtt_work > 0
        and (not command_target_fresh or mqtt_work_target_after_command)
        and (not planned_ids or mqtt_work in planned_ids)
    ):
        return [mqtt_work], "mqtt_work_target"

    if command_target_fresh and command_ids:
        return [command_ids[0]], "ha_command"

    if mqtt_work_target_fresh and mqtt_work is not None and mqtt_work > 0:
        if not planned_ids or mqtt_work in planned_ids:
            return [mqtt_work], "mqtt_work_target"

    cloud_work = _as_int(cloud_work_target)
    if (
        cloud_work is not None
        and cloud_work > 0
        and (not planned_ids or cloud_work in planned_ids)
    ):
        return [cloud_work], "private_work_target"

    if len(planned_ids) == 1:
        return planned_ids, "planned_single_zone"

    return [], "none"

def _target_state(snapshot: dict[str, Any], zone_ids: Any) -> str:
    """Render public task targets using current map names."""
    ids = _zone_ids(zone_ids)
    map_data = snapshot.get("map") or {}
    zones = map_data.get("zones") or snapshot.get("zones") or []
    names: dict[int, str] = {}
    for row in zones:
        if not isinstance(row, dict):
            continue
        zone_id = _as_int(row.get("id"))
        if zone_id is not None:
            names[zone_id] = str(row.get("name") or f"Zone {zone_id}")
    labels = [names.get(zone_id, f"Zone {zone_id}") for zone_id in ids]
    return ", ".join(labels) if labels else "No active target"


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
        command_target_ids = _zone_ids(
            getattr(self, "_command_target_zone_ids", [])
        )
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

        def _publish_task_target(current: dict[str, Any]) -> dict[str, Any]:
            # Preserve the gate/navigation owner separately. The public Target
            # zone below is intentionally task-only and must never feed back into
            # gate arbitration.
            navigation_ids = _zone_ids(
                current.get("navigation_target_zone_ids")
                if "navigation_target_zone_ids" in current
                else current.get("target_zone_ids")
            )
            navigation_source = str(
                current.get("navigation_target_zone_source")
                or current.get("target_zone_source")
                or "none"
            )
            navigation_state = str(
                current.get("navigation_target_zone")
                or current.get("target_zone")
                or "No active target"
            )
            if current.get("target_zone_source") == "same_zone_command_guard":
                navigation_ids = _zone_ids(current.get("target_zone_ids"))
                navigation_source = "same_zone_command_guard"
                navigation_state = str(
                    current.get("target_zone") or _target_state(snapshot, navigation_ids)
                )

            current["navigation_target_zone"] = navigation_state
            current["navigation_target_zone_ids"] = navigation_ids
            current["navigation_target_zone_source"] = navigation_source

            is_docked = bool(snapshot.get("docked")) or navigation_source == "docked"
            is_returning = (
                str(snapshot.get("activity") or "").strip().lower() == "returning"
                or navigation_source == "returning_to_dock"
            )
            task_active = _task_target_active(snapshot)
            planned_ids, planned_source = _resolve_public_task_target(
                is_docked=is_docked,
                is_returning=is_returning,
                task_active=task_active,
                command_target_ids=command_target_ids,
                command_target_fresh=bool(current.get("command_target_active")),
                mqtt_partition_ids=sanitized.get("partition_ids"),
                mqtt_partition_fresh=bool(freshness["partition_ids_fresh"]),
                cloud_zone_ids=snapshot.get("current_zone_ids"),
                mqtt_work_target=sanitized.get("work_target_zone"),
                mqtt_work_target_fresh=bool(freshness["work_target_fresh"]),
                cloud_work_target=snapshot.get("work_target_zone"),
            )

            command_set_at = getattr(self, "_command_target_set_at", None)
            work_target_set_at = getattr(
                self,
                "_mqtt_work_target_last_update",
                None,
            )
            work_target_after_command = bool(
                command_set_at is None
                or (
                    isinstance(work_target_set_at, (int, float))
                    and isinstance(command_set_at, (int, float))
                    and work_target_set_at >= command_set_at
                )
            )
            immediate_ids, immediate_source = _resolve_immediate_target(
                is_docked=is_docked,
                is_returning=is_returning,
                task_active=task_active,
                command_target_ids=command_target_ids,
                command_target_fresh=bool(current.get("command_target_active")),
                planned_zone_ids=planned_ids,
                mqtt_work_target=sanitized.get("work_target_zone"),
                mqtt_work_target_fresh=bool(freshness["work_target_fresh"]),
                mqtt_work_target_after_command=work_target_after_command,
                cloud_work_target=snapshot.get("work_target_zone"),
            )

            # target_zone_ids remains the task-selection compatibility key
            # used by History/Zone Ledger/Scheduler. New code should use the
            # explicit planned-zone fields for that meaning.
            current["planned_zone_ids"] = planned_ids
            current["planned_zones_source"] = planned_source
            current["planned_zones"] = _target_state(snapshot, planned_ids)
            current["planned_zones_task_active"] = task_active
            current["target_zone_ids"] = planned_ids

            current["target_zone_id"] = (
                immediate_ids[0] if len(immediate_ids) == 1 else None
            )
            current["target_zone_source"] = immediate_source
            current["target_zone"] = _target_state(snapshot, immediate_ids)
            current["target_zone_task_active"] = task_active
            return current

        guard = getattr(self, "_same_zone_command_gate_guard", None)
        if not isinstance(guard, dict):
            return _publish_task_target(result)

        zone_id = _as_int(guard.get("zone_id"))
        age = _age_seconds(guard.get("started_at"), now)
        if (
            zone_id is None
            or age is None
            or age > _SAME_ZONE_COMMAND_GUARD_SECONDS
        ):
            self._same_zone_command_gate_guard = None  # noqa: SLF001
            return _publish_task_target(result)

        # Unknown/stale position is never evidence that a transition ended.
        if not _physical_zone_is_fresh(result, zone_id):
            self._same_zone_command_gate_guard = None  # noqa: SLF001
            return _publish_task_target(result)
        if _mapped_channel_active(result):
            self._same_zone_command_gate_guard = None  # noqa: SLF001
            return _publish_task_target(result)

        target_ids = _coordinator._dedupe_zone_ids(result.get("target_zone_ids"))  # noqa: SLF001
        target_source = str(result.get("target_zone_source") or "")
        if target_source == "returning_to_dock":
            self._same_zone_command_gate_guard = None  # noqa: SLF001
            return _publish_task_target(result)

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
            return _publish_task_target(result)

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
        return _publish_task_target(result)

    _mqtt.parse_location_payload = parse_location_payload
    _fallback._risky_cloud_gate_transition = _strict_cloud_gate_transition  # noqa: SLF001
    cls.ingest_mqtt_location = ingest_mqtt_location
    cls.set_command_target = set_command_target
    cls.clear_command_target = clear_command_target
    cls.channel_state = channel_state
    cls._navigation_fields = navigation_fields
    cls._navigation_intent_installed = True
