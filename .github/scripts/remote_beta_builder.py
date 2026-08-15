from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

ROOT = Path.cwd()
COMPONENT = ROOT / "custom_components" / "navimower"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one marker, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).lstrip(), encoding="utf-8")


# Release identity.
manifest_path = COMPONENT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != "0.4.3-beta12":
    raise SystemExit(f"Expected 0.4.3-beta12 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta13"
manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


write(COMPONENT / "schedule_logic.py", r'''
"""Pure decision helpers for Navimower-managed mowing windows."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Iterable


def parse_hhmm(value: Any, default: str) -> time:
    """Return a local wall-clock time from ``HH:MM`` or a time object."""
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = str(value or default).strip()
    try:
        hour_s, minute_s = text.split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (TypeError, ValueError):
        hour_s, minute_s = default.split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
    return time(hour=hour, minute=minute)


def format_hhmm(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def window_state(now: datetime, start: time, end: time) -> tuple[bool, str | None]:
    """Return whether ``now`` is in the daily window and its stable start-date token."""
    local_time = now.timetz().replace(tzinfo=None)
    if start == end:
        return False, None
    if start < end:
        if start <= local_time < end:
            return True, now.date().isoformat()
        return False, None
    if local_time >= start:
        return True, now.date().isoformat()
    if local_time < end:
        return True, (now.date() - timedelta(days=1)).isoformat()
    return False, None


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def later_iso(first: Any, second: Any) -> str | None:
    """Return the later valid ISO timestamp."""
    a, b = parse_iso(first), parse_iso(second)
    if a is None and b is None:
        return None
    if a is None:
        return str(second)
    if b is None:
        return str(first)
    return str(second) if b > a else str(first)


def completion_advanced(current: Any, baseline: Any, dispatched_at: Any) -> bool:
    """Accept completion only when it is newer than both baseline and this dispatch."""
    current_dt = parse_iso(current)
    dispatch_dt = parse_iso(dispatched_at)
    if current_dt is None or dispatch_dt is None or current_dt <= dispatch_dt:
        return False
    baseline_dt = parse_iso(baseline)
    return baseline_dt is None or current_dt > baseline_dt


def _minimum_aware_datetime() -> datetime:
    return datetime.min.replace(tzinfo=datetime.now().astimezone().tzinfo)


def select_oldest_zone(
    zones: Iterable[dict[str, Any]],
    *,
    completed_in_window: set[int] | None = None,
    just_completed_zone_id: int | None = None,
    scheduler_completed_at: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Choose the eligible zone whose confirmed completion is oldest."""
    completed = completed_in_window or set()
    confirmed = scheduler_completed_at or {}
    candidates: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for row in zones or []:
        if not isinstance(row, dict):
            continue
        try:
            zone_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        if zone_id <= 0 or zone_id in completed or zone_id == just_completed_zone_id:
            continue
        effective = later_iso(row.get("last_completed_at"), confirmed.get(str(zone_id)))
        parsed = parse_iso(effective)
        key = (0, _minimum_aware_datetime()) if parsed is None else (1, parsed)
        candidates.append(((key[0], key[1], zone_id), row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]
''')


write(COMPONENT / "navimower_schedule.py", r'''
"""Navimower-owned one-zone-at-a-time mowing schedule."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, time
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ACTIVITY_DOCKED,
    ACTIVITY_ERROR,
    ACTIVITY_MOWING,
    ACTIVITY_PAUSED,
    ACTIVITY_RETURNING,
    DOMAIN,
    encode_partition_ids,
    mow_setup,
)
from .resume import async_resume_task
from .schedule_logic import (
    completion_advanced,
    format_hhmm,
    later_iso,
    parse_hhmm,
    parse_iso,
    select_oldest_zone,
    window_state,
)
from .setting_write import async_write_settings

_LOGGER = logging.getLogger(__name__)

OPT_SCHEDULE_ENABLED = "navimower_schedule_enabled"
OPT_SCHEDULE_START = "navimower_schedule_start"
OPT_SCHEDULE_END = "navimower_schedule_end"
DEFAULT_SCHEDULE_START = "10:00"
DEFAULT_SCHEDULE_END = "20:00"

_STORE_VERSION = 1
_TICK_SECONDS = 20
_RESUME_CONFIRM_SECONDS = 90
_CONTINUE_CONFIRM_SECONDS = 120
_DOCK_RETRY_SECONDS = 60
_RETRY_NEW_MOW_SECONDS = 30


def schedule_store(hass: HomeAssistant, entry_id: str) -> Store:
    key = f"{DOMAIN}_managed_schedule_{entry_id}"
    try:
        return Store(hass, _STORE_VERSION, key, serialize_in_event_loop=False)
    except TypeError:
        return Store(hass, _STORE_VERSION, key)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _age_seconds(value: Any) -> float | None:
    parsed = parse_iso(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds())


class NavimowerScheduleController:
    """Own a daily mowing window while leaving charging decisions to the mower."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._store = schedule_store(hass, entry.entry_id)
        self._enabled = bool(entry.options.get(OPT_SCHEDULE_ENABLED, False))
        self._start = parse_hhmm(entry.options.get(OPT_SCHEDULE_START), DEFAULT_SCHEDULE_START)
        self._end = parse_hhmm(entry.options.get(OPT_SCHEDULE_END), DEFAULT_SCHEDULE_END)
        self._runtime: dict[str, Any] = self._empty_runtime()
        self._unsub = None
        self._tick_task: asyncio.Task | None = None
        self._evaluate_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._stopped = False

    @staticmethod
    def _empty_runtime() -> dict[str, Any]:
        return {
            "window_token": None,
            "completed_zone_ids_in_window": [],
            "active_zone_id": None,
            "active_cycle_id": None,
            "active_zone_baseline_completed_at": None,
            "dispatch_started_at": None,
            "just_completed_zone_id": None,
            "scheduler_completed_at": {},
            "resume_pending": False,
            "interrupted_zone_id": None,
            "interrupted_cycle_id": None,
            "progress_before_interrupt": None,
            "pending_command": None,
            "retry_not_before": None,
            "last_command": None,
            "last_command_at": None,
            "last_error": None,
            "suspended_reason": None,
        }

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def start_time(self) -> time:
        return self._start

    @property
    def end_time(self) -> time:
        return self._end

    async def async_start(self) -> None:
        try:
            saved = await self._store.async_load()
        except Exception:
            saved = None
        if isinstance(saved, dict):
            restored = self._empty_runtime()
            restored.update({key: deepcopy(value) for key, value in saved.items() if key in restored})
            self._runtime = restored
        self._unsub = self.coordinator.async_add_listener(self._handle_update)
        self._tick_task = self.hass.async_create_background_task(
            self._tick_loop(),
            f"Navimower managed schedule {self.entry.entry_id}",
        )
        self._queue_evaluation()

    async def async_stop(self) -> None:
        self._stopped = True
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._tick_task is not None:
            self._tick_task.cancel()
            await asyncio.gather(self._tick_task, return_exceptions=True)
            self._tick_task = None
        if self._evaluate_task is not None:
            await asyncio.gather(self._evaluate_task, return_exceptions=True)
            self._evaluate_task = None
        await self._save()

    @staticmethod
    async def async_remove_all(hass: HomeAssistant, entry_id: str) -> None:
        try:
            await schedule_store(hass, entry_id).async_remove()
        except Exception:
            return

    def diagnostics(self) -> dict[str, Any]:
        now = dt_util.now()
        open_now, token = window_state(now, self._start, self._end)
        return {
            "enabled": self._enabled,
            "start": format_hhmm(self._start),
            "end": format_hhmm(self._end),
            "window_open": open_now,
            "window_token_now": token,
            **deepcopy(self._runtime),
        }

    def entity_attributes(self) -> dict[str, Any]:
        row = self.diagnostics()
        return {
            key: row.get(key)
            for key in (
                "start",
                "end",
                "window_open",
                "active_zone_id",
                "resume_pending",
                "interrupted_zone_id",
                "last_command",
                "last_error",
                "suspended_reason",
            )
        }

    async def async_set_enabled(self, enabled: bool, *, reason: str) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        if enabled:
            native = (self.coordinator.data or {}).get("settings", {}).get("schedule_enabled")
            if native is None:
                raise RuntimeError("Native mowing schedule state is not available yet")
            if native is True:
                await self._async_set_native_schedule(False)
            self._runtime = self._empty_runtime()
            self._runtime["last_command"] = f"enabled:{reason}"
            self._runtime["last_command_at"] = _utc_now()
        else:
            confirmed = deepcopy(self._runtime.get("scheduler_completed_at") or {})
            self._runtime = self._empty_runtime()
            self._runtime["scheduler_completed_at"] = confirmed
            self._runtime["last_command"] = f"disabled:{reason}"
            self._runtime["last_command_at"] = _utc_now()
        self._enabled = enabled
        self._update_options(**{OPT_SCHEDULE_ENABLED: enabled})
        await self._save()
        if enabled:
            await self.async_evaluate()

    async def async_set_window(self, key: str, value: time) -> None:
        new_value = value.replace(second=0, microsecond=0)
        start = new_value if key == "start" else self._start
        end = new_value if key == "end" else self._end
        if start == end:
            raise ValueError("Navimower schedule start and end must be different")
        if key == "start":
            self._start = new_value
            option_key = OPT_SCHEDULE_START
        elif key == "end":
            self._end = new_value
            option_key = OPT_SCHEDULE_END
        else:
            raise ValueError(f"Unknown schedule time key: {key}")
        self._update_options(**{option_key: format_hhmm(new_value)})
        self._queue_evaluation()

    def _update_options(self, **updates: Any) -> None:
        options = dict(self.entry.options)
        options.update(updates)
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    async def _async_set_native_schedule(self, on: bool) -> None:
        value = "1" if on else "0"
        await async_write_settings(
            self.coordinator,
            operations=(
                (
                    self.coordinator.client.send_setting_device,
                    (self.coordinator.sn, {"startPlan": value}),
                ),
                (
                    self.coordinator.client.set_iot_bool,
                    (
                        self.coordinator.sn,
                        self.coordinator.vehicle_type,
                        "startPlan",
                        on,
                        False,
                    ),
                ),
            ),
            cache_values={"startPlan": value},
        )

    @callback
    def _handle_update(self) -> None:
        self._queue_evaluation()

    def _queue_evaluation(self) -> None:
        if self._stopped:
            return
        if self._evaluate_task is not None and not self._evaluate_task.done():
            return
        self._evaluate_task = self.hass.async_create_task(
            self.async_evaluate(),
            f"Navimower schedule evaluate {self.entry.entry_id}",
        )

    async def _tick_loop(self) -> None:
        try:
            while not self._stopped:
                await asyncio.sleep(_TICK_SECONDS)
                await self.async_evaluate()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Navimower schedule tick failed")

    def _zones(self) -> list[dict[str, Any]]:
        return [row for row in (self.coordinator.data or {}).get("zone_states") or [] if isinstance(row, dict)]

    def _zone(self, zone_id: int | None) -> dict[str, Any] | None:
        if zone_id is None:
            return None
        for row in self._zones():
            try:
                if int(row.get("id")) == int(zone_id):
                    return row
            except (TypeError, ValueError):
                continue
        return None

    def _progress_for_zone(self, zone_id: int | None) -> float | None:
        data = self.coordinator.data or {}
        if zone_id is not None and data.get("active_zone_progress_zone_id") == zone_id:
            try:
                return float(data.get("active_zone_progress"))
            except (TypeError, ValueError):
                pass
        row = self._zone(zone_id)
        if row is not None:
            try:
                return float(row.get("coverage_pct"))
            except (TypeError, ValueError):
                pass
        try:
            return float(data.get("mowing_progress"))
        except (TypeError, ValueError):
            return None

    def _pending_age(self) -> float | None:
        pending = self._runtime.get("pending_command")
        return _age_seconds(pending.get("sent_at")) if isinstance(pending, dict) else None

    def _retry_ready(self) -> bool:
        stamp = self._runtime.get("retry_not_before")
        parsed = parse_iso(stamp)
        return parsed is None or datetime.now(UTC) >= parsed.astimezone(UTC)

    async def async_evaluate(self) -> None:
        if self._stopped:
            return
        async with self._lock:
            try:
                await self._evaluate_locked()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                self._runtime["last_error"] = f"{type(err).__name__}: {err}"
                _LOGGER.warning("Navimower schedule evaluation failed: %s", err)
                await self._save()

    async def _evaluate_locked(self) -> None:
        if not self._enabled:
            return
        data = self.coordinator.data or {}
        settings = data.get("settings") or {}
        if settings.get("schedule_enabled") is True:
            await self.async_set_enabled(False, reason="native_schedule_enabled")
            return

        now = dt_util.now()
        in_window, token = window_state(now, self._start, self._end)
        if in_window and token and token != self._runtime.get("window_token"):
            self._runtime["window_token"] = token
            self._runtime["completed_zone_ids_in_window"] = []
            self._runtime["just_completed_zone_id"] = None
            self._runtime["suspended_reason"] = None
            self._runtime["retry_not_before"] = None
            await self._save()

        completed_now = await self._confirm_active_completion()
        activity = data.get("activity")
        await self._confirm_pending(activity)

        if not in_window:
            await self._enforce_closed_window(activity)
            return
        if self._runtime.get("suspended_reason"):
            return

        if self._runtime.get("resume_pending"):
            if activity == ACTIVITY_MOWING:
                self._runtime["resume_pending"] = False
                self._runtime["pending_command"] = None
                self._runtime["last_command"] = "retained_task_already_mowing"
                self._runtime["last_command_at"] = _utc_now()
                await self._save()
                return
            await self._continue_interrupted_task(activity)
            return

        if self._runtime.get("active_zone_id") is not None:
            return

        pending = self._runtime.get("pending_command")
        if isinstance(pending, dict):
            return
        if not self._retry_ready():
            return
        if activity not in {ACTIVITY_DOCKED, ACTIVITY_PAUSED} and not (
            completed_now and activity == ACTIVITY_MOWING
        ):
            return

        completed = {int(value) for value in self._runtime.get("completed_zone_ids_in_window") or []}
        candidate = select_oldest_zone(
            self._zones(),
            completed_in_window=completed,
            just_completed_zone_id=self._runtime.get("just_completed_zone_id"),
            scheduler_completed_at=self._runtime.get("scheduler_completed_at") or {},
        )
        if candidate is None:
            return
        try:
            zone_id = int(candidate["id"])
        except (KeyError, TypeError, ValueError):
            return
        await self._async_send_mow(zone_id, reset=True, source="navimower_schedule_next_zone")

    async def _confirm_active_completion(self) -> bool:
        zone_id = self._runtime.get("active_zone_id")
        if zone_id is None:
            return False
        row = self._zone(int(zone_id))
        if row is None:
            return False
        current = row.get("last_completed_at")
        if not completion_advanced(
            current,
            self._runtime.get("active_zone_baseline_completed_at"),
            self._runtime.get("dispatch_started_at"),
        ):
            return False
        stamp = str(current)
        completed = {int(value) for value in self._runtime.get("completed_zone_ids_in_window") or []}
        completed.add(int(zone_id))
        self._runtime["completed_zone_ids_in_window"] = sorted(completed)
        self._runtime["just_completed_zone_id"] = int(zone_id)
        confirmed = dict(self._runtime.get("scheduler_completed_at") or {})
        confirmed[str(zone_id)] = later_iso(confirmed.get(str(zone_id)), stamp) or stamp
        self._runtime["scheduler_completed_at"] = confirmed
        self._runtime["active_zone_id"] = None
        self._runtime["active_cycle_id"] = None
        self._runtime["active_zone_baseline_completed_at"] = None
        self._runtime["dispatch_started_at"] = None
        self._runtime["resume_pending"] = False
        self._runtime["interrupted_zone_id"] = None
        self._runtime["interrupted_cycle_id"] = None
        self._runtime["progress_before_interrupt"] = None
        self._runtime["pending_command"] = None
        self._runtime["retry_not_before"] = None
        self._runtime["last_command"] = f"zone_completed:{zone_id}"
        self._runtime["last_command_at"] = _utc_now()
        self._runtime["last_error"] = None
        await self._save()
        return True

    async def _confirm_pending(self, activity: Any) -> None:
        pending = self._runtime.get("pending_command")
        if not isinstance(pending, dict):
            return
        kind = str(pending.get("kind") or "")
        if kind in {"mow", "resume", "continue"} and activity == ACTIVITY_MOWING:
            self._runtime["pending_command"] = None
            if kind in {"resume", "continue"}:
                self._runtime["resume_pending"] = False
            self._runtime["last_error"] = None
            await self._save()
        elif kind == "dock" and activity in {ACTIVITY_RETURNING, ACTIVITY_DOCKED}:
            self._runtime["pending_command"] = None
            await self._save()

    async def _enforce_closed_window(self, activity: Any) -> None:
        zone_id = self._runtime.get("active_zone_id")
        if zone_id is not None and not self._runtime.get("resume_pending"):
            self._runtime["resume_pending"] = True
            self._runtime["interrupted_zone_id"] = int(zone_id)
            self._runtime["interrupted_cycle_id"] = self._runtime.get("active_cycle_id")
            self._runtime["progress_before_interrupt"] = self._progress_for_zone(int(zone_id))
            await self._save()

        if activity not in {ACTIVITY_MOWING, ACTIVITY_PAUSED}:
            return
        pending = self._runtime.get("pending_command")
        age = self._pending_age()
        if isinstance(pending, dict) and pending.get("kind") == "dock" and age is not None and age < _DOCK_RETRY_SECONDS:
            return
        await self._async_send_dock("navimower_schedule_window_closed")

    async def _continue_interrupted_task(self, activity: Any) -> None:
        zone_id = self._runtime.get("interrupted_zone_id") or self._runtime.get("active_zone_id")
        if zone_id is None:
            self._runtime["resume_pending"] = False
            await self._save()
            return
        pending = self._runtime.get("pending_command")
        age = self._pending_age()
        if isinstance(pending, dict):
            kind = pending.get("kind")
            if kind == "resume" and age is not None and age >= _RESUME_CONFIRM_SECONDS:
                self._runtime["pending_command"] = None
                await self._async_send_mow(int(zone_id), reset=False, source="navimower_schedule_continue_fallback")
            elif kind == "continue" and age is not None and age >= _CONTINUE_CONFIRM_SECONDS:
                self._runtime["pending_command"] = None
                self._runtime["suspended_reason"] = "interrupted_task_continue_not_confirmed"
                self._runtime["last_error"] = "Resume and one-zone continue were not confirmed; automatic reset was refused"
                await self._save()
            return
        if activity in {ACTIVITY_ERROR, ACTIVITY_RETURNING}:
            return
        try:
            await async_resume_task(self.coordinator, source="navimower_schedule_window_resume")
        except Exception as err:
            self._runtime["last_error"] = f"Resume failed: {type(err).__name__}: {err}"
            await self._async_send_mow(int(zone_id), reset=False, source="navimower_schedule_continue_fallback")
            return
        self._runtime["pending_command"] = {"kind": "resume", "zone_id": int(zone_id), "sent_at": _utc_now()}
        self._runtime["last_command"] = f"resume:{zone_id}"
        self._runtime["last_command_at"] = _utc_now()
        await self._save()

    async def _async_send_mow(self, zone_id: int, *, reset: bool, source: str) -> None:
        row = self._zone(zone_id) or {}
        partition_ids = encode_partition_ids([zone_id])
        partition_setup = mow_setup(reset=reset, ordered=False)
        self.coordinator.begin_mow_command_trace(
            source=source,
            requested_zone_ids=[zone_id],
            resolved_zone_ids=[zone_id],
            reset=reset,
            ordered=False,
            partition_ids_hex=partition_ids,
            partition_setup=partition_setup,
        )
        self.coordinator.set_pending_activity(ACTIVITY_MOWING)
        self.coordinator.set_command_target([zone_id], source=source)
        sent_at = _utc_now()
        try:
            result = await self.coordinator.async_send(
                self.coordinator.client.mow_zones,
                self.coordinator.sn,
                partition_ids,
                partition_setup,
            )
            self.coordinator.record_mow_command_result(result)
        except Exception as err:
            self.coordinator.record_mow_command_error(err)
            self.coordinator.clear_pending_activity()
            self.coordinator.clear_command_target()
            self._runtime["last_error"] = f"{source} failed: {type(err).__name__}: {err}"
            if reset:
                retry_at = datetime.now(UTC).timestamp() + _RETRY_NEW_MOW_SECONDS
                self._runtime["retry_not_before"] = datetime.fromtimestamp(retry_at, UTC).isoformat()
            else:
                self._runtime["suspended_reason"] = "interrupted_task_continue_failed"
            await self._save()
            return

        if reset:
            self.coordinator.start_new_mowing_cycle([zone_id], source=source)
            self._runtime["active_zone_id"] = zone_id
            self._runtime["active_cycle_id"] = row.get("cycle_id")
            self._runtime["active_zone_baseline_completed_at"] = row.get("last_completed_at")
            self._runtime["dispatch_started_at"] = sent_at
            self._runtime["just_completed_zone_id"] = None
            self._runtime["retry_not_before"] = None
        else:
            self._runtime["active_zone_id"] = int(self._runtime.get("active_zone_id") or zone_id)
            self._runtime["resume_pending"] = True
        self._runtime["pending_command"] = {
            "kind": "mow" if reset else "continue",
            "zone_id": zone_id,
            "reset": reset,
            "sent_at": sent_at,
        }
        self._runtime["last_command"] = f"mow:{zone_id}:reset={str(reset).lower()}"
        self._runtime["last_command_at"] = sent_at
        self._runtime["last_error"] = None
        await self._save()

    async def _async_send_dock(self, source: str) -> None:
        self.coordinator.clear_command_target()
        self.coordinator.set_pending_activity(ACTIVITY_RETURNING)
        center = getattr(self.coordinator, "notification_center", None)
        if center is not None:
            center.note_dock_command(source)
        sent_at = _utc_now()
        try:
            await self.coordinator.async_send(self.coordinator.client.dock, self.coordinator.sn)
        except Exception as err:
            if center is not None:
                center.clear_dock_command()
            self.coordinator.clear_pending_activity()
            self._runtime["last_error"] = f"Dock failed: {type(err).__name__}: {err}"
            await self._save()
            return
        self._runtime["pending_command"] = {"kind": "dock", "sent_at": sent_at}
        self._runtime["last_command"] = "dock:window_closed"
        self._runtime["last_command_at"] = sent_at
        await self._save()

    async def _save(self) -> None:
        try:
            await self._store.async_save(deepcopy(self._runtime))
        except Exception:
            _LOGGER.debug("Could not persist Navimower schedule state", exc_info=True)
''')


init_path = COMPONENT / "__init__.py"
replace_once(
    init_path,
    "from .notification_center import NavimowerNotificationCenter\nfrom .oauth import async_register_oauth_implementation\n",
    "from .notification_center import NavimowerNotificationCenter\nfrom .navimower_schedule import NavimowerScheduleController\nfrom .oauth import async_register_oauth_implementation\n",
    "schedule controller import",
)
replace_once(
    init_path,
    '''    if not coordinator.data:\n        coordinator.async_set_updated_data(coordinator.bootstrap_snapshot())\n\n    coordinator.private_poll_guard_task = hass.async_create_background_task(\n''',
    '''    if not coordinator.data:\n        coordinator.async_set_updated_data(coordinator.bootstrap_snapshot())\n\n    navimower_schedule = NavimowerScheduleController(hass, entry, coordinator)\n    coordinator.navimower_schedule = navimower_schedule\n    await navimower_schedule.async_start()\n\n    coordinator.private_poll_guard_task = hass.async_create_background_task(\n''',
    "schedule controller setup",
)
replace_once(
    init_path,
    '''    session_archive = (\n        getattr(coordinator, "session_archive", None) if coordinator else None\n    )\n    private_poll_guard = (\n''',
    '''    session_archive = (\n        getattr(coordinator, "session_archive", None) if coordinator else None\n    )\n    navimower_schedule = (\n        getattr(coordinator, "navimower_schedule", None) if coordinator else None\n    )\n    private_poll_guard = (\n''',
    "schedule controller unload reference",
)
replace_once(
    init_path,
    '''    if notification_center is not None:\n        await notification_center.async_stop()\n''',
    '''    if navimower_schedule is not None:\n        await navimower_schedule.async_stop()\n    if notification_center is not None:\n        await notification_center.async_stop()\n''',
    "schedule controller unload",
)
replace_once(
    init_path,
    '''    await NavimowerNotificationCenter.async_remove_all(hass, entry.entry_id)\n    await SessionArchiveManager.async_remove_all(hass, entry.entry_id)\n''',
    '''    await NavimowerNotificationCenter.async_remove_all(hass, entry.entry_id)\n    await NavimowerScheduleController.async_remove_all(hass, entry.entry_id)\n    await SessionArchiveManager.async_remove_all(hass, entry.entry_id)\n''',
    "schedule store cleanup",
)


notification_center = COMPONENT / "notification_center.py"
replace_once(
    notification_center,
    '        content += ". The command source was not Home Assistant, so Navimower does not assume it came from the mobile app."\n',
    '        content += "."\n',
    "external task notification wording",
)


switch_path = COMPONENT / "switch.py"
replace_once(
    switch_path,
    '''    async_add_entities(\n        NavimowSwitch(coordinator, desc) for desc in supported_descriptions\n    )\n\n\nclass NavimowSwitch''',
    '''    entities = [NavimowSwitch(coordinator, desc) for desc in supported_descriptions]\n    if getattr(coordinator, "navimower_schedule", None) is not None:\n        entities.append(NavimowerScheduleSwitch(coordinator))\n    async_add_entities(entities)\n\n\nclass NavimowerScheduleSwitch(NavimowEntity, SwitchEntity):\n    """Enable the integration-owned daily mowing window."""\n\n    _attr_name = "Navimower schedule"\n    _attr_icon = "mdi:calendar-sync"\n\n    def __init__(self, coordinator: NavimowCoordinator) -> None:\n        super().__init__(coordinator, "navimower_schedule")\n        self.controller = coordinator.navimower_schedule\n\n    @property\n    def is_on(self) -> bool:\n        return bool(self.controller.enabled)\n\n    @property\n    def extra_state_attributes(self) -> dict[str, Any]:\n        return self.controller.entity_attributes()\n\n    async def async_turn_on(self, **kwargs: Any) -> None:\n        await self.controller.async_set_enabled(True, reason="home_assistant_switch")\n        self.async_write_ha_state()\n\n    async def async_turn_off(self, **kwargs: Any) -> None:\n        await self.controller.async_set_enabled(False, reason="home_assistant_switch")\n        self.async_write_ha_state()\n\n\nclass NavimowSwitch''',
    "managed schedule switch entity",
)
replace_once(
    switch_path,
    '''    async def _write(self, on: bool) -> None:\n        desc = self.entity_description\n        operations = []\n''',
    '''    async def _write(self, on: bool) -> None:\n        desc = self.entity_description\n        if desc.key == "mowing_schedule_enabled" and on:\n            controller = getattr(self.coordinator, "navimower_schedule", None)\n            if controller is not None and controller.enabled:\n                await controller.async_set_enabled(False, reason="native_schedule_enabled_from_home_assistant")\n        operations = []\n''',
    "native schedule mutual exclusion",
)


write(COMPONENT / "time.py", r'''
"""Local time controls for the Navimower-managed mowing window."""
from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NavimowCoordinator
from .entity import NavimowEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NavimowCoordinator = hass.data[DOMAIN][entry.entry_id]
    controller = getattr(coordinator, "navimower_schedule", None)
    if controller is None:
        return
    async_add_entities(
        [
            NavimowerScheduleTime(coordinator, "start"),
            NavimowerScheduleTime(coordinator, "end"),
        ]
    )


class NavimowerScheduleTime(NavimowEntity, TimeEntity):
    """Start or end of the integration-owned mowing window."""

    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: NavimowCoordinator, key: str) -> None:
        super().__init__(coordinator, f"navimower_schedule_{key}")
        self.controller = coordinator.navimower_schedule
        self._key = key
        self._attr_name = f"Navimower schedule {key}"

    @property
    def native_value(self) -> time:
        return self.controller.start_time if self._key == "start" else self.controller.end_time

    async def async_set_value(self, value: time) -> None:
        await self.controller.async_set_window(self._key, value)
        self.async_write_ha_state()
''')


diagnostics_path = COMPONENT / "diagnostics.py"
replace_once(
    diagnostics_path,
    '''    notification_center_diagnostics = (\n        notification_center.diagnostics()\n        if notification_center is not None\n        and hasattr(notification_center, "diagnostics")\n        else None\n    )\n\n    history_index = (\n''',
    '''    notification_center_diagnostics = (\n        notification_center.diagnostics()\n        if notification_center is not None\n        and hasattr(notification_center, "diagnostics")\n        else None\n    )\n    navimower_schedule = getattr(coordinator, "navimower_schedule", None)\n    navimower_schedule_diagnostics = (\n        navimower_schedule.diagnostics()\n        if navimower_schedule is not None and hasattr(navimower_schedule, "diagnostics")\n        else None\n    )\n\n    history_index = (\n''',
    "schedule diagnostics snapshot",
)
replace_once(
    diagnostics_path,
    '''        "settings": sanitize(deepcopy(settings)),\n        "map": sanitize(\n''',
    '''        "settings": sanitize(deepcopy(settings)),\n        "navimower_schedule": sanitize(deepcopy(navimower_schedule_diagnostics)),\n        "map": sanitize(\n''',
    "schedule diagnostics output",
)
replace_once(
    diagnostics_path,
    '            "0.4.3-beta12 keeps Maintenance/Mowing Reports discovery paused and bounds Clear and resume / Reboot Mower public-H5 recovery so Download diagnostics always returns partial evidence instead of waiting on the crawler.",\n',
    '            "The integration-owned one-zone schedule is disabled by default and exposes only its persisted runtime state in diagnostics; bounded error-command discovery remains unchanged.",\n',
    "managed schedule diagnostics note",
)


write(ROOT / ".github/release-notes/0.4.3-beta13.md", r'''
## Navimower 0.4.3-beta13

This beta adds the first integration-owned **Navimower schedule** for field testing.

### Added
- A disabled-by-default `Navimower schedule` switch plus local start/end time entities.
- One-zone-at-a-time orchestration: the next zone is the eligible zone with the oldest confirmed `last_completed_at` value.
- Per-window completion memory and a just-completed exclusion guard so delayed cloud data cannot immediately select the same zone again.
- Window-end interruption handling: Dock/Home is sent, the interrupted zone is retained, and the next window first tries vendor Resume then one-zone `mow(reset=false)` if Resume is not confirmed.
- Scheduler state in Download diagnostics for field testing.

### Safety
- Native mower schedule and Navimower schedule are mutually exclusive. Enabling Navimower schedule disables the native schedule; re-enabling the native schedule disables Navimower schedule.
- Charging and low-battery return remain mower-owned.
- An interrupted task never falls back automatically to `reset=true`. If Resume and continue cannot be confirmed, the scheduler suspends instead of restarting the zone from zero.
- A zone completion must advance beyond both its pre-dispatch `last_completed_at` and the scheduler dispatch timestamp before another zone is selected.

### Changed
- External mowing notifications no longer include the confusing command-source attribution sentence.
''')

changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
heading = "# Changelog\n\n"
if not changelog.startswith(heading):
    raise SystemExit("Unexpected changelog header")
section = dedent(r'''
## 0.4.3-beta13

Integration-owned one-zone mowing window for field testing.

### Added

- Add a disabled-by-default Navimower schedule switch and configurable local start/end time entities.
- Select one zone at a time by the oldest confirmed `last_completed_at`, with per-window completion and just-completed race guards.
- At window close, retain the interrupted zone and send Dock/Home; at the next window try Resume first and `mow(reset=false)` second.
- Persist scheduler runtime state across Home Assistant restarts and expose it in Download diagnostics.

### Changed

- Make native mower schedule and Navimower schedule mutually exclusive.
- Remove the command-source caveat from External mowing task started notifications.

### Safety

- Leave low-battery charging to the mower.
- Never use automatic `reset=true` as an interrupted-task fallback; suspend instead if Resume and continue cannot be confirmed.

''')
changelog_path.write_text(heading + section + changelog[len(heading):], encoding="utf-8")


write(ROOT / "tests/test_navimower_schedule_logic.py", r'''
from datetime import datetime, time, timezone
import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "navimower" / "schedule_logic.py"
spec = importlib.util.spec_from_file_location("navimower_schedule_logic", MODULE_PATH)
assert spec and spec.loader
logic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logic)
completion_advanced = logic.completion_advanced
select_oldest_zone = logic.select_oldest_zone
window_state = logic.window_state


def test_same_day_window():
    now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    assert window_state(now, time(10, 0), time(20, 0)) == (True, "2026-08-15")
    assert window_state(now, time(13, 0), time(20, 0)) == (False, None)


def test_cross_midnight_window_uses_start_date_token():
    early = datetime(2026, 8, 16, 1, 0, tzinfo=timezone.utc)
    assert window_state(early, time(20, 0), time(2, 0)) == (True, "2026-08-15")


def test_oldest_zone_and_guards():
    zones = [
        {"id": 1, "last_completed_at": "2026-08-14T10:00:00+00:00"},
        {"id": 2, "last_completed_at": "2026-08-13T10:00:00+00:00"},
        {"id": 3, "last_completed_at": None},
    ]
    assert select_oldest_zone(zones)["id"] == 3
    assert select_oldest_zone(zones, completed_in_window={3})["id"] == 2
    assert select_oldest_zone(zones, completed_in_window={3}, just_completed_zone_id=2)["id"] == 1


def test_scheduler_confirmed_completion_beats_stale_cloud_value():
    zones = [
        {"id": 1, "last_completed_at": "2026-08-10T10:00:00+00:00"},
        {"id": 2, "last_completed_at": "2026-08-11T10:00:00+00:00"},
    ]
    confirmed = {"1": "2026-08-15T10:00:00+00:00"}
    assert select_oldest_zone(zones, scheduler_completed_at=confirmed)["id"] == 2


def test_completion_must_be_newer_than_baseline_and_dispatch():
    baseline = "2026-08-14T10:00:00+00:00"
    dispatch = "2026-08-15T10:00:00+00:00"
    assert not completion_advanced(baseline, baseline, dispatch)
    assert not completion_advanced("2026-08-15T09:59:59+00:00", baseline, dispatch)
    assert completion_advanced("2026-08-15T10:30:00+00:00", baseline, dispatch)
''')

write(ROOT / "tests/test_v043_beta13.py", r'''
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta13_identity_and_schedule_modules():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta13"
    assert (COMPONENT / "navimower_schedule.py").exists()
    assert (COMPONENT / "schedule_logic.py").exists()


def test_schedule_is_disabled_by_default_and_mutually_exclusive():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    switch = (COMPONENT / "switch.py").read_text(encoding="utf-8")
    assert 'entry.options.get(OPT_SCHEDULE_ENABLED, False)' in source
    assert 'settings.get("schedule_enabled") is True' in source
    assert 'await self._async_set_native_schedule(False)' in source
    assert 'native_schedule_enabled_from_home_assistant' in switch


def test_resume_then_continue_without_automatic_reset_fallback():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    assert 'async_resume_task(self.coordinator, source="navimower_schedule_window_resume")' in source
    assert 'reset=False, source="navimower_schedule_continue_fallback"' in source
    assert 'automatic reset was refused' in source
    assert 'interrupted_task_continue_failed' in source


def test_completion_and_same_zone_race_guards_are_wired():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    assert 'completion_advanced(' in source
    assert 'completed_zone_ids_in_window' in source
    assert 'just_completed_zone_id' in source
    assert 'scheduler_completed_at' in source
    assert 'last_mowed_at' not in source


def test_external_notification_caveat_removed():
    source = (COMPONENT / "notification_center.py").read_text(encoding="utf-8")
    assert "The command source was not Home Assistant" not in source


def test_diagnostics_and_time_controls_are_exposed():
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    time_source = (COMPONENT / "time.py").read_text(encoding="utf-8")
    assert '"navimower_schedule": sanitize' in diagnostics
    assert 'Navimower schedule {key}' in time_source
''')
