"""Persistent Navimower-generated notifications and mowing-task attribution."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
import re
import time
from typing import Any

from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from .const import (
    ACTIVITY_DOCKED,
    ACTIVITY_MOWING,
    ACTIVITY_RETURNING,
    DOMAIN,
    MQTT_DOCKED_STATES,
    MQTT_STATE_MOWING,
    MQTT_STATE_RETURNING,
    VEHICLE_STATE_TO_ACTIVITY,
)

LOCAL_NOTIFICATION_PREFIX = "navimower:"
LOCAL_NOTIFICATION_LIMIT = 20
VENDOR_NOTIFICATION_LIMIT = 10
MERGED_NOTIFICATION_LIMIT = LOCAL_NOTIFICATION_LIMIT + VENDOR_NOTIFICATION_LIMIT

_LOCAL_STORE_VERSION = 1
_COMMAND_CONTEXT_SECONDS = 180
_RESUME_CONTEXT_SECONDS = 120
_DOCK_CONTEXT_SECONDS = 180
_SCHEDULE_START_GRACE_MINUTES = 30
_SCHEDULE_END_GRACE_MINUTES = 25
_NIGHT_BEFORE_SUNSET_MINUTES = 30
_NIGHT_AFTER_SUNSET_MINUTES = 120
_SUNRISE_RESUME_WINDOW_HOURS = 6


def notification_store(hass: HomeAssistant, entry_id: str) -> Store:
    """Return the persistent per-entry local-notification Store."""
    key = f"{DOMAIN}_notifications_{entry_id}"
    try:
        return Store(hass, _LOCAL_STORE_VERSION, key, serialize_in_event_loop=False)
    except TypeError:
        return Store(hass, _LOCAL_STORE_VERSION, key)


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe_ints(values: Any) -> list[int]:
    result: list[int] = []
    for item in values or []:
        value = _as_int(item)
        if value is not None and value > 0 and value not in result:
            result.append(value)
    return result


def _message_timestamp(item: dict[str, Any]) -> float:
    raw = _as_float(item.get("addtime"))
    if raw is not None:
        return raw / 1000.0 if raw > 10_000_000_000 else raw
    created = dt_util.parse_datetime(str(item.get("created_at") or ""))
    if created is None:
        return 0.0
    return dt_util.as_utc(created).timestamp()


def merge_notification_lists(
    vendor_messages: list[dict[str, Any]] | None,
    local_messages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge bounded vendor/local lists newest-first without duplicate IDs."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*(vendor_messages or []), *(local_messages or [])]:
        if not isinstance(item, dict):
            continue
        message_id = str(item.get("message_id") or item.get("id") or "").strip()
        if message_id and message_id in seen:
            continue
        if message_id:
            seen.add(message_id)
        merged.append(deepcopy(item))
    merged.sort(key=_message_timestamp, reverse=True)
    return merged[:MERGED_NOTIFICATION_LIMIT]


def _safe_key(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-")
    return text[:72] or str(int(datetime.now(UTC).timestamp()))


def _zone_names(snapshot: dict[str, Any]) -> dict[int, str]:
    result: dict[int, str] = {}
    map_data = snapshot.get("map") if isinstance(snapshot.get("map"), dict) else {}
    rows = map_data.get("zones") or snapshot.get("zones") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        zone_id = _as_int(row.get("id"))
        if zone_id is None:
            continue
        result[zone_id] = str(row.get("name") or f"Zone {zone_id}")
    return result


def _zone_phrase(names: list[str] | None) -> str:
    values = [str(value) for value in (names or []) if value]
    if not values or values == ["All zones"]:
        return "all zones"
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])} and {values[-1]}"


def _ordered_zone_phrase(names: list[str] | None) -> str:
    values = [str(value) for value in (names or []) if value]
    return " -> ".join(values) if values else "all zones"


class NavimowerNotificationCenter:
    """Own local notifications, task context and transition attribution."""

    def __init__(self, hass: HomeAssistant, entry_id: str, coordinator: Any) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.coordinator = coordinator
        self._store = notification_store(hass, entry_id)
        self._messages: list[dict[str, Any]] = []
        self._active_task: dict[str, Any] | None = None
        self._interrupted_reason: str | None = None
        self._observed_activity: str | None = None
        self._persisted_activity: str | None = None
        self._last_mowing_progress: float | None = None
        self._last_mowing_battery: float | None = None
        self._last_ha_dock_mono: float | None = None
        self._consumed_mow_trace: str | None = None
        self._consumed_resume_trace: str | None = None
        self._unsub = None
        self._transition_lock = asyncio.Lock()

    @property
    def messages(self) -> list[dict[str, Any]]:
        """Return a safe bounded copy of local notifications."""
        return deepcopy(self._messages[:LOCAL_NOTIFICATION_LIMIT])

    @property
    def active_task(self) -> dict[str, Any] | None:
        return deepcopy(self._active_task)

    @property
    def interrupted_reason(self) -> str | None:
        return self._interrupted_reason

    async def async_load(self) -> None:
        """Restore local messages/read state and retained task context."""
        try:
            payload = await self._store.async_load()
        except Exception:  # noqa: BLE001 - optional history must never block setup.
            payload = None
        if not isinstance(payload, dict):
            payload = {}

        loaded: list[dict[str, Any]] = []
        for item in payload.get("messages") or []:
            if not isinstance(item, dict):
                continue
            message_id = str(item.get("message_id") or "")
            if not message_id.startswith(LOCAL_NOTIFICATION_PREFIX):
                continue
            loaded.append(deepcopy(item))
        loaded.sort(key=_message_timestamp, reverse=True)
        self._messages = loaded[:LOCAL_NOTIFICATION_LIMIT]

        task = payload.get("active_task")
        self._active_task = deepcopy(task) if isinstance(task, dict) else None
        reason = payload.get("interrupted_reason")
        self._interrupted_reason = str(reason) if reason else None
        persisted_activity = payload.get("last_activity")
        self._persisted_activity = str(persisted_activity) if persisted_activity else None
        self._consumed_mow_trace = (
            str(payload.get("consumed_mow_trace"))
            if payload.get("consumed_mow_trace")
            else None
        )
        self._consumed_resume_trace = (
            str(payload.get("consumed_resume_trace"))
            if payload.get("consumed_resume_trace")
            else None
        )

    def start(self) -> None:
        """Start observing coordinator state transitions before entities load."""
        if self._unsub is not None:
            return
        self._observed_activity = self._persisted_activity or self._confirmed_activity(
            self.coordinator.data or {}
        )
        self._unsub = self.coordinator.async_add_listener(self._handle_update)

    async def async_stop(self) -> None:
        """Stop observing and flush local state."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        await self._async_save()

    @staticmethod
    async def async_remove_all(hass: HomeAssistant, entry_id: str) -> None:
        try:
            await notification_store(hass, entry_id).async_remove()
        except Exception:  # noqa: BLE001
            return

    def note_dock_command(self, source: str) -> None:
        """Remember a fresh Home Assistant Dock intent for stop attribution."""
        self._last_ha_dock_mono = time.monotonic()
        if self._active_task is not None:
            self._active_task["last_dock_source"] = str(source)

    def clear_dock_command(self) -> None:
        self._last_ha_dock_mono = None

    async def async_mark_read(self, message_id: str) -> bool:
        """Mark one Navimower-local message read and publish immediately."""
        target = str(message_id or "").strip()
        found = False
        changed = False
        for item in self._messages:
            if str(item.get("message_id") or "") == target:
                found = True
                if item.get("read") is not True:
                    item["read"] = True
                    changed = True
                break
        if changed:
            await self._async_save()
            self._publish()
        return found

    async def async_mark_all_read(self) -> int:
        """Mark every retained local notification read."""
        changed = 0
        for item in self._messages:
            if item.get("read") is not True:
                item["read"] = True
                changed += 1
        if changed:
            await self._async_save()
            self._publish()
        return changed

    def diagnostics(self) -> dict[str, Any]:
        """Return non-secret task/notification attribution diagnostics."""
        return {
            "local_count": len(self._messages),
            "active_task": deepcopy(self._active_task),
            "interrupted_reason": self._interrupted_reason,
            "observed_activity": self._observed_activity,
            "last_mowing_progress": self._last_mowing_progress,
            "last_mowing_battery": self._last_mowing_battery,
            "mqtt_mow_start_type": self._mqtt_value("mow_start_type"),
            "mqtt_task_delay": self._mqtt_value("task_delay"),
        }

    def _confirmed_activity(self, snapshot: dict[str, Any]) -> str | None:
        """Return only vendor-confirmed work state, never optimistic HA activity."""
        mqtt_state = _as_int(snapshot.get("mqtt_vehicle_state"))
        if mqtt_state == MQTT_STATE_MOWING:
            return ACTIVITY_MOWING
        if mqtt_state == MQTT_STATE_RETURNING:
            return ACTIVITY_RETURNING
        if mqtt_state in MQTT_DOCKED_STATES:
            return ACTIVITY_DOCKED

        state_code = str(snapshot.get("state_code") or "")
        mapped = VEHICLE_STATE_TO_ACTIVITY.get(state_code)
        if mapped in {ACTIVITY_MOWING, ACTIVITY_RETURNING, ACTIVITY_DOCKED}:
            return mapped

        # Unknown/transient vendor states do not create synthetic transitions.
        # Keep the previous confirmed state until a known private/MQTT state lands.
        return self._observed_activity

    @callback
    def _handle_update(self) -> None:
        snapshot = self.coordinator.data or {}
        current = self._confirmed_activity(snapshot)

        if current == ACTIVITY_MOWING:
            progress = _as_float(snapshot.get("mowing_progress"))
            battery = _as_float(snapshot.get("battery"))
            if progress is not None:
                self._last_mowing_progress = progress
            if battery is not None:
                self._last_mowing_battery = battery

        previous = self._observed_activity
        if current == previous:
            return
        self._observed_activity = current

        # The first observed state after a fresh install is a baseline, not an
        # invented historical start/stop event. Persisted activity survives HA
        # restarts so real transitions that occurred while HA was down can still
        # be attributed on the first refresh after restart.
        if previous is None:
            self._persisted_activity = current
            return

        state = deepcopy(snapshot)
        self.hass.async_create_task(
            self._async_process_transition(previous, current, state),
            f"Navimower notification transition {self.entry_id}",
        )

    async def _async_process_transition(
        self,
        previous: str | None,
        current: str | None,
        snapshot: dict[str, Any],
    ) -> None:
        async with self._transition_lock:
            emitted = False
            if current == ACTIVITY_MOWING and previous != ACTIVITY_MOWING:
                emitted = self._handle_mowing_start(snapshot)
            elif previous == ACTIVITY_MOWING and current in {
                ACTIVITY_RETURNING,
                ACTIVITY_DOCKED,
            }:
                emitted = self._handle_mowing_stop(snapshot)

            self._persisted_activity = current
            await self._async_save()
            if emitted:
                self._publish()

    def _handle_mowing_start(self, snapshot: dict[str, Any]) -> bool:
        now_local = dt_util.now()

        resume_trace = self._recent_resume_trace()
        if resume_trace is not None:
            key = str(resume_trace.get("requested_at") or "")
            self._consumed_resume_trace = key or self._consumed_resume_trace
            names = self._task_zone_names(snapshot)
            if self._active_task is not None:
                names = list(self._active_task.get("zone_names") or names)
            content = "Resumed the vendor-retained interrupted mowing task"
            if names:
                content += f" in {_zone_phrase(names)}"
            content += " using the Navimower Resume command."
            item = self._emit(
                "NM1006",
                "Mowing resumed",
                content,
                kind="mowing_resumed",
                confidence="confirmed_ha_command",
                event_key=key or None,
            )
            self._ensure_task_context(
                snapshot,
                task_id=(self._active_task or {}).get("task_id")
                or (item or {}).get("message_id"),
                origin=(self._active_task or {}).get("origin") or "retained",
                trigger=str(resume_trace.get("source") or "navimower.resume"),
                zone_names=names,
            )
            self._interrupted_reason = None
            return item is not None

        mow_trace = self._recent_mow_trace()
        if mow_trace is not None:
            key = str(mow_trace.get("started_at_utc") or "")
            self._consumed_mow_trace = key or self._consumed_mow_trace
            explicit = bool(mow_trace.get("explicit_zone_selection"))
            ordered = bool(mow_trace.get("ordered"))
            reset = bool(mow_trace.get("reset"))
            names = [
                str(value)
                for value in (
                    mow_trace.get("requested_zone_names")
                    if explicit
                    else mow_trace.get("resolved_zone_names")
                )
                or []
                if value
            ]
            if not explicit:
                target = "all zones"
            elif ordered:
                target = _ordered_zone_phrase(names)
            else:
                target = _zone_phrase(names)
            content = f"Started mowing {target}. Start from scratch is {'on' if reset else 'off'}."
            if explicit and not ordered:
                content += " Zone order is selected by the mower."
            item = self._emit(
                "NM1002",
                "Mowing task started",
                content,
                kind="ha_mowing_started",
                confidence="confirmed_ha_command",
                event_key=key or None,
            )
            selected_ids = (
                mow_trace.get("requested_zone_ids")
                if explicit
                else mow_trace.get("resolved_zone_ids")
            )
            self._active_task = {
                "task_id": (item or {}).get("message_id") or key,
                "origin": "home_assistant",
                "trigger": str(mow_trace.get("source") or "ha_mow"),
                "zone_ids": list(selected_ids or []),
                "zone_names": names,
                "ordered": ordered,
                "reset": reset,
                "started_at": datetime.now(UTC).isoformat(),
            }
            self._interrupted_reason = None
            return item is not None

        if self._active_task is not None and self._interrupted_reason == "night":
            sunrise = self._sun_event_local(SUN_EVENT_SUNRISE, now_local.date())
            if sunrise is not None and sunrise <= now_local <= sunrise + timedelta(
                hours=_SUNRISE_RESUME_WINDOW_HOURS
            ):
                names = list(self._active_task.get("zone_names") or [])
                content = "Resumed the unfinished mowing task after sunrise"
                if names:
                    content += f" in {_zone_phrase(names)}"
                content += "."
                if self._active_task.get("origin") != "schedule":
                    next_start = self._next_schedule_start_today(snapshot, now_local)
                    if next_start:
                        content += (
                            " This is a continuation of the previous task; today's "
                            f"scheduled mowing starts at {next_start}."
                        )
                    else:
                        content += " This is a continuation of the previous task."
                item = self._emit(
                    "NM1005",
                    "Unfinished mowing resumed after sunrise",
                    content,
                    kind="sunrise_resume",
                    confidence="inferred_from_retained_task_and_sunrise",
                    event_key=f"{self._task_token()}-{now_local.date().isoformat()}",
                )
                self._active_task["last_resumed_at"] = datetime.now(UTC).isoformat()
                self._interrupted_reason = None
                return item is not None

        if self._active_task is not None and self._interrupted_reason == "charging":
            names = list(self._active_task.get("zone_names") or [])
            before = self._active_task.get("progress_before_pause")
            content = "Resumed the unfinished mowing task after charging"
            if names:
                content += f" in {_zone_phrase(names)}"
            if before is not None:
                content += f". Progress before charging was {before:g}%"
            content += "."
            item = self._emit(
                "NM1008",
                "Mowing resumed after charging",
                content,
                kind="charging_resume",
                confidence="inferred_from_retained_task_and_charge_pause",
            )
            self._active_task["last_resumed_at"] = datetime.now(UTC).isoformat()
            self._interrupted_reason = None
            return item is not None

        schedule_period = self._schedule_start_candidate(snapshot, now_local)
        if schedule_period is not None:
            names = list(schedule_period.get("zone_names") or [])
            start_dt = schedule_period["start_dt"]
            end_dt = schedule_period["end_dt"]
            content = (
                f"The mower started scheduled mowing in {_zone_phrase(names)}. "
                f"Scheduled window ends at {end_dt.strftime('%H:%M')}."
            )
            night_mow = (snapshot.get("settings") or {}).get("night_mow")
            sunset = self._sun_event_local(SUN_EVENT_SUNSET, now_local.date())
            if (
                night_mow is False
                and sunset is not None
                and start_dt < sunset < end_dt
            ):
                content += (
                    f" Night mowing is off; sunset is around {sunset.strftime('%H:%M')}, "
                    "so mowing may pause before the scheduled window ends."
                )
            event_key = (
                f"{now_local.date().isoformat()}-{schedule_period['start_min']}-"
                f"{schedule_period['end_min']}"
            )
            item = self._emit(
                "NM1001",
                "Scheduled mowing started",
                content,
                kind="scheduled_mowing_started",
                confidence="schedule_window_match",
                event_key=event_key,
            )
            self._active_task = {
                "task_id": (item or {}).get("message_id") or event_key,
                "origin": "schedule",
                "trigger": "schedule",
                "zone_ids": list(schedule_period.get("zone_ids") or []),
                "zone_names": names,
                "ordered": False,
                "started_at": datetime.now(UTC).isoformat(),
                "schedule_start": start_dt.isoformat(),
                "schedule_end": end_dt.isoformat(),
                "night_mowing": night_mow,
            }
            self._interrupted_reason = None
            return item is not None

        ids = self._observed_task_zone_ids(snapshot)
        names_by_id = _zone_names(snapshot)
        names = [names_by_id.get(value, f"Zone {value}") for value in ids]
        content = "Started an external mowing task"
        if names:
            content += f" in {_zone_phrase(names)}"
        else:
            content += " for all zones or a target list not exposed by the mower"
        content += ". The command source was not Home Assistant, so Navimower does not assume it came from the mobile app."
        item = self._emit(
            "NM1003",
            "External mowing task started",
            content,
            kind="external_mowing_started",
            confidence="observed_external_start",
        )
        self._active_task = {
            "task_id": (item or {}).get("message_id"),
            "origin": "external",
            "trigger": "external_or_vendor",
            "zone_ids": ids,
            "zone_names": names,
            "ordered": None,
            "started_at": datetime.now(UTC).isoformat(),
            "observed_mow_start_type": self._mqtt_value("mow_start_type"),
        }
        self._interrupted_reason = None
        return item is not None

    def _handle_mowing_stop(self, snapshot: dict[str, Any]) -> bool:
        now_local = dt_util.now()
        progress = self._last_mowing_progress
        if progress is None:
            progress = _as_float(snapshot.get("mowing_progress"))
        battery = self._last_mowing_battery
        if battery is None:
            battery = _as_float(snapshot.get("battery"))

        if self._active_task is None:
            self._ensure_task_context(
                snapshot,
                task_id=f"observed-{int(datetime.now(UTC).timestamp())}",
                origin="observed",
                trigger="observed_active_task",
                zone_names=self._task_zone_names(snapshot),
            )
        if self._active_task is not None:
            self._active_task["progress_before_pause"] = progress
            self._active_task["battery_before_pause"] = battery

        # 100% is intentionally strict here. Vendor history may accept lower
        # practical completion percentages, but a local user-facing "completed"
        # notification should not be invented from an ambiguous 95-99% return.
        if progress is not None and progress >= 100:
            names = list((self._active_task or {}).get("zone_names") or [])
            content = "Completed the mowing task"
            if names:
                content += f" in {_zone_phrase(names)}"
            content += "."
            item = self._emit(
                "NM1009",
                "Mowing task completed",
                content,
                kind="mowing_completed",
                confidence="confirmed_100_percent_transition",
                event_key=self._task_token(),
            )
            self._active_task = None
            self._interrupted_reason = None
            return item is not None

        if self._recent_ha_dock():
            content = "Home Assistant sent the mower to the dock"
            if progress is not None:
                content += f" while the mowing task was at {progress:g}%"
            content += ". Navimower keeps the unfinished task context for later Resume attribution."
            item = self._emit(
                "NM1011",
                "Mower sent to dock",
                content,
                kind="ha_dock_interruption",
                confidence="confirmed_ha_command",
            )
            self._interrupted_reason = "manual_dock"
            return item is not None

        night_candidate = self._night_pause_candidate(snapshot, now_local)
        schedule_end = self._active_schedule_end()
        if night_candidate is not None and (
            schedule_end is None or night_candidate < schedule_end - timedelta(minutes=5)
        ):
            names = list((self._active_task or {}).get("zone_names") or [])
            content = "The unfinished mowing task started returning to the charging station around sunset because Night mowing is disabled"
            if names:
                content += f" while mowing {_zone_phrase(names)}"
            if progress is not None:
                content += f" at {progress:g}% progress"
            content += ". It may resume after sunrise when the mower retains the task and charging allows."
            item = self._emit(
                "NM1004",
                "Mowing paused for night",
                content,
                kind="night_pause",
                confidence="inferred_from_sunset_and_night_mowing_off",
                event_key=f"{self._task_token()}-{now_local.date().isoformat()}",
            )
            self._interrupted_reason = "night"
            if self._active_task is not None:
                self._active_task["night_paused_at"] = datetime.now(UTC).isoformat()
            return item is not None

        if schedule_end is not None:
            delta = abs((now_local - schedule_end).total_seconds())
            if delta <= _SCHEDULE_END_GRACE_MINUTES * 60:
                names = list((self._active_task or {}).get("zone_names") or [])
                content = f"The scheduled mowing window ended at {schedule_end.strftime('%H:%M')}"
                if names:
                    content += f" for {_zone_phrase(names)}"
                if progress is not None:
                    content += f" with task progress at {progress:g}%"
                content += "."
                item = self._emit(
                    "NM1010",
                    "Scheduled mowing window ended",
                    content,
                    kind="scheduled_window_ended",
                    confidence="schedule_end_transition",
                    event_key=f"{self._task_token()}-{schedule_end.isoformat()}",
                )
                self._active_task = None
                self._interrupted_reason = None
                return item is not None

        threshold = _as_float((snapshot.get("settings") or {}).get("return_battery_level"))
        if battery is not None and threshold is not None and battery <= threshold + 2:
            names = list((self._active_task or {}).get("zone_names") or [])
            content = f"The unfinished mowing task returned to charge at {battery:g}% battery"
            if names:
                content += f" while mowing {_zone_phrase(names)}"
            if progress is not None:
                content += f" at {progress:g}% task progress"
            content += "."
            item = self._emit(
                "NM1007",
                "Mowing paused for charging",
                content,
                kind="charging_pause",
                confidence="inferred_from_return_battery_threshold",
            )
            self._interrupted_reason = "charging"
            if self._active_task is not None:
                self._active_task["charging_paused_at"] = datetime.now(UTC).isoformat()
            return item is not None

        self._interrupted_reason = "unknown"
        return False

    def _recent_mow_trace(self) -> dict[str, Any] | None:
        trace = getattr(self.coordinator, "_last_mow_command_trace", None)
        if not isinstance(trace, dict) or trace.get("send_error"):
            return None
        key = str(trace.get("started_at_utc") or "")
        if not key or key == self._consumed_mow_trace:
            return None
        started = _as_float(trace.get("_started_monotonic"))
        if started is None:
            return None
        age = time.monotonic() - started
        return trace if 0 <= age <= _COMMAND_CONTEXT_SECONDS else None

    def _recent_resume_trace(self) -> dict[str, Any] | None:
        trace = getattr(self.coordinator, "_last_resume_command", None)
        if not isinstance(trace, dict):
            return None
        key = str(trace.get("requested_at") or "")
        if not key or key == self._consumed_resume_trace or trace.get("error"):
            return None
        requested = dt_util.parse_datetime(key)
        if requested is None:
            return None
        age = (datetime.now(UTC) - dt_util.as_utc(requested)).total_seconds()
        return trace if 0 <= age <= _RESUME_CONTEXT_SECONDS else None

    def _recent_ha_dock(self) -> bool:
        if self._last_ha_dock_mono is None:
            return False
        age = time.monotonic() - self._last_ha_dock_mono
        if 0 <= age <= _DOCK_CONTEXT_SECONDS:
            self._last_ha_dock_mono = None
            return True
        self._last_ha_dock_mono = None
        return False

    def _schedule_start_candidate(
        self, snapshot: dict[str, Any], now_local: datetime
    ) -> dict[str, Any] | None:
        settings = snapshot.get("settings") or {}
        if settings.get("schedule_enabled") is False:
            return None
        weekday = (now_local.weekday() + 1) % 7 + 1
        minutes = now_local.hour * 60 + now_local.minute
        day_start = dt_util.start_of_local_day(now_local)
        for row in snapshot.get("schedule") or []:
            if not isinstance(row, dict) or not row.get("enabled"):
                continue
            if _as_int(row.get("day")) != weekday:
                continue
            for period in row.get("periods") or []:
                if not isinstance(period, dict):
                    continue
                start_min = _as_int(period.get("start_min"))
                end_min = _as_int(period.get("end_min"))
                if start_min is None or end_min is None or end_min <= start_min:
                    continue
                if not (start_min <= minutes <= start_min + _SCHEDULE_START_GRACE_MINUTES):
                    continue
                return {
                    **period,
                    "start_min": start_min,
                    "end_min": end_min,
                    "start_dt": day_start + timedelta(minutes=start_min),
                    "end_dt": day_start + timedelta(minutes=end_min),
                }
        return None

    def _next_schedule_start_today(
        self, snapshot: dict[str, Any], now_local: datetime
    ) -> str | None:
        settings = snapshot.get("settings") or {}
        if settings.get("schedule_enabled") is False:
            return None
        weekday = (now_local.weekday() + 1) % 7 + 1
        minutes = now_local.hour * 60 + now_local.minute
        starts: list[int] = []
        for row in snapshot.get("schedule") or []:
            if not isinstance(row, dict) or not row.get("enabled"):
                continue
            if _as_int(row.get("day")) != weekday:
                continue
            for period in row.get("periods") or []:
                start_min = _as_int((period or {}).get("start_min"))
                if start_min is not None and start_min > minutes:
                    starts.append(start_min)
        if not starts:
            return None
        value = min(starts)
        return f"{(value // 60) % 24:02d}:{value % 60:02d}"

    def _night_pause_candidate(
        self, snapshot: dict[str, Any], now_local: datetime
    ) -> datetime | None:
        if (snapshot.get("settings") or {}).get("night_mow") is not False:
            return None
        sunset = self._sun_event_local(SUN_EVENT_SUNSET, now_local.date())
        if sunset is None:
            return None
        delta = (now_local - sunset).total_seconds()
        if -_NIGHT_BEFORE_SUNSET_MINUTES * 60 <= delta <= _NIGHT_AFTER_SUNSET_MINUTES * 60:
            return sunset
        return None

    def _active_schedule_end(self) -> datetime | None:
        if not self._active_task or self._active_task.get("origin") != "schedule":
            return None
        value = self._active_task.get("schedule_end")
        parsed = dt_util.parse_datetime(str(value or ""))
        return dt_util.as_local(parsed) if parsed is not None else None

    def _sun_event_local(self, event: str, day: date) -> datetime | None:
        try:
            value = get_astral_event_date(self.hass, event, day)
        except Exception:  # noqa: BLE001 - polar/no-location edge cases are optional context.
            return None
        return dt_util.as_local(value) if value is not None else None

    def _observed_task_zone_ids(self, snapshot: dict[str, Any]) -> list[int]:
        ids = _dedupe_ints(self._mqtt_value("partition_ids"))
        if ids:
            return ids
        ids = _dedupe_ints((snapshot.get("totals") or {}).get("task_zone_ids"))
        if ids:
            return ids
        return _dedupe_ints(snapshot.get("target_zone_ids"))

    def _task_zone_names(self, snapshot: dict[str, Any]) -> list[str]:
        if self._active_task is not None and self._active_task.get("zone_names"):
            return [str(value) for value in self._active_task.get("zone_names") or []]
        ids = self._observed_task_zone_ids(snapshot)
        names = _zone_names(snapshot)
        return [names.get(value, f"Zone {value}") for value in ids]

    def _mqtt_value(self, key: str) -> Any:
        location = getattr(self.coordinator, "_mqtt_location", None)
        return location.get(key) if isinstance(location, dict) else None

    def _ensure_task_context(
        self,
        snapshot: dict[str, Any],
        *,
        task_id: Any,
        origin: str,
        trigger: str,
        zone_names: list[str] | None = None,
    ) -> None:
        if self._active_task is None:
            ids = self._observed_task_zone_ids(snapshot)
            names = zone_names or self._task_zone_names(snapshot)
            self._active_task = {
                "task_id": task_id,
                "origin": origin,
                "trigger": trigger,
                "zone_ids": ids,
                "zone_names": list(names or []),
                "ordered": None,
                "started_at": datetime.now(UTC).isoformat(),
            }
        else:
            self._active_task["trigger"] = trigger

    def _task_token(self) -> str:
        task_id = (self._active_task or {}).get("task_id")
        return _safe_key(task_id or int(datetime.now(UTC).timestamp()))

    def _emit(
        self,
        code: str,
        title: str,
        content: str,
        *,
        kind: str,
        confidence: str,
        event_key: Any | None = None,
    ) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        key = _safe_key(event_key if event_key is not None else int(now.timestamp()))
        message_id = f"{LOCAL_NOTIFICATION_PREFIX}{code}:{key}"
        if any(str(item.get("message_id")) == message_id for item in self._messages):
            return None
        item = {
            "id": message_id,
            "message_id": message_id,
            "title": str(title)[:255],
            "content": str(content)[:1200],
            "addtime": int(now.timestamp()),
            "created_at": now.isoformat(),
            "read": False,
            "level": 1,
            "type": 1,
            "style": 1,
            "variable": "",
            "notification_code": str(code),
            "vendor_code": None,
            "error_code": None,
            "event_code": str(code),
            "origin": "navimower",
            "kind": str(kind),
            "confidence": str(confidence),
        }
        self._messages.insert(0, item)
        self._messages.sort(key=_message_timestamp, reverse=True)
        del self._messages[LOCAL_NOTIFICATION_LIMIT:]
        return item

    async def _async_save(self) -> None:
        payload = {
            "messages": deepcopy(self._messages[:LOCAL_NOTIFICATION_LIMIT]),
            "active_task": deepcopy(self._active_task),
            "interrupted_reason": self._interrupted_reason,
            "last_activity": self._persisted_activity or self._observed_activity,
            "consumed_mow_trace": self._consumed_mow_trace,
            "consumed_resume_trace": self._consumed_resume_trace,
        }
        try:
            await self._store.async_save(payload)
        except Exception:  # noqa: BLE001 - notification persistence must not break mower control.
            return

    def _publish(self) -> None:
        """Re-decorate the current snapshot without making any vendor request."""
        try:
            from .notification_feed import refresh_notification_snapshot

            refresh_notification_snapshot(self.coordinator)
        except Exception:  # noqa: BLE001 - local notifications are supplementary.
            return
