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
    DEFAULT_SCHEDULE_END,
    DEFAULT_SCHEDULE_MODE,
    DEFAULT_SCHEDULE_ORDER_MODE,
    DEFAULT_SCHEDULE_START,
    DOMAIN,
    MQTT_STATE_CHARGING,
    MQTT_STATE_MOWING,
    OPT_SCHEDULE_ENABLED,
    OPT_SCHEDULE_END,
    OPT_SCHEDULE_MODE,
    OPT_SCHEDULE_ORDER_MODE,
    OPT_SCHEDULE_CUSTOM_QUEUE,
    OPT_SCHEDULE_START,
    OPT_SCHEDULE_ZONE_IDS,
    SCHEDULE_MODE_CONTINUOUS,
    SCHEDULE_ORDER_AUTOMATIC,
    SCHEDULE_ORDER_CUSTOM,
    SCHEDULE_MODE_WINDOW,
    STATE_IDLE_DOCKED_POST,
    STATE_MOWING,
    STATE_MOWING_MANUAL,
    encode_partition_ids,
    mow_setup,
)
from .resume import async_resume_task
from .schedule_logic import (
    completion_advanced,
    filter_schedule_zones,
    format_hhmm,
    later_iso,
    parse_hhmm,
    parse_iso,
    select_oldest_zone,
    window_state,
)
from .setting_write import async_write_settings

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1
_TICK_SECONDS = 20
_RESUME_CONFIRM_SECONDS = 90
_CONTINUE_CONFIRM_SECONDS = 120
_MOW_CONFIRM_SECONDS = 120
_DOCK_RETRY_SECONDS = 60
_RETRY_NEW_MOW_SECONDS = 30
_LOW_BATTERY_RESUME_GRACE_SECONDS = 180


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


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


class NavimowerScheduleController:
    """Own a daily mowing window while leaving charging decisions to the mower."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._store = schedule_store(hass, entry.entry_id)
        self._enabled = bool(entry.options.get(OPT_SCHEDULE_ENABLED, False))
        mode = str(entry.options.get(OPT_SCHEDULE_MODE, DEFAULT_SCHEDULE_MODE))
        self._mode = mode if mode in {SCHEDULE_MODE_WINDOW, SCHEDULE_MODE_CONTINUOUS} else DEFAULT_SCHEDULE_MODE
        self._start = parse_hhmm(entry.options.get(OPT_SCHEDULE_START), DEFAULT_SCHEDULE_START)
        self._end = parse_hhmm(entry.options.get(OPT_SCHEDULE_END), DEFAULT_SCHEDULE_END)
        order_mode = str(entry.options.get(OPT_SCHEDULE_ORDER_MODE, DEFAULT_SCHEDULE_ORDER_MODE))
        self._order_mode = order_mode if order_mode in {SCHEDULE_ORDER_AUTOMATIC, SCHEDULE_ORDER_CUSTOM} else DEFAULT_SCHEDULE_ORDER_MODE
        self._custom_queue = self._normalize_queue(entry.options.get(OPT_SCHEDULE_CUSTOM_QUEUE, []))
        self._selection_configured = OPT_SCHEDULE_ZONE_IDS in entry.options
        self._selected_zone_ids = self._normalize_zone_ids(entry.options.get(OPT_SCHEDULE_ZONE_IDS, []))
        self._legacy_selection_migration_allowed = self._enabled and not self._selection_configured
        self._runtime: dict[str, Any] = self._empty_runtime()
        self._unsub = None
        self._tick_task: asyncio.Task | None = None
        self._evaluate_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._stopped = False

    @staticmethod
    def _normalize_zone_ids(values: Any) -> set[int]:
        if not isinstance(values, (list, tuple, set)):
            values = [] if values in (None, "") else [values]
        result: set[int] = set()
        for value in values:
            try:
                zone_id = int(value)
            except (TypeError, ValueError):
                continue
            if zone_id > 0:
                result.add(zone_id)
        return result

    @staticmethod
    def _normalize_queue(values: Any) -> list[int]:
        if isinstance(values, str):
            values = [item.strip() for item in values.split(",") if item.strip()]
        if not isinstance(values, (list, tuple)):
            return []
        result = []
        for value in values:
            try:
                zone_id = int(value)
            except (TypeError, ValueError):
                continue
            if zone_id > 0:
                result.append(zone_id)
        return result

    @staticmethod
    def _empty_runtime() -> dict[str, Any]:
        return {
            "window_token": None,
            "round_index": 1,
            "round_started_at": None,
            "completed_zone_ids_in_window": [],
            "completed_queue_slots": [],
            "active_queue_slot": None,
            "active_zone_id": None,
            "active_cycle_id": None,
            "active_zone_baseline_completed_at": None,
            "dispatch_started_at": None,
            "just_completed_zone_id": None,
            "scheduler_completed_at": {},
            "resume_pending": False,
            "interrupted_reason": None,
            "interrupted_zone_id": None,
            "interrupted_cycle_id": None,
            "progress_before_interrupt": None,
            "charging_limit_reached_at": None,
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
    def mode(self) -> str:
        return self._mode

    @property
    def selected_zone_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self._selected_zone_ids))

    @property
    def configured(self) -> bool:
        """Return whether the user has saved Navimower Schedule setup."""
        return self._selection_configured

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
        self._maybe_migrate_legacy_zone_selection()
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
        open_now, token = self._window_state(now)
        eligible = self._eligible_zones()
        eligible_ids = sorted(int(row["id"]) for row in eligible if row.get("id") is not None)
        return {
            "enabled": self._enabled,
            "mode": self._mode,
            "order_mode": self._order_mode,
            "custom_queue": list(self._custom_queue),
            "start": format_hhmm(self._start),
            "end": format_hhmm(self._end),
            "selected_zone_ids": sorted(self._selected_zone_ids),
            "eligible_zone_ids": eligible_ids,
            "zone_selection_configured": self._selection_configured,
            "window_open": open_now,
            "window_token_now": token,
            **deepcopy(self._runtime),
        }

    def entity_attributes(self) -> dict[str, Any]:
        row = self.diagnostics()
        return {
            key: row.get(key)
            for key in (
                "mode",
                "order_mode",
                "custom_queue",
                "start",
                "end",
                "selected_zone_ids",
                "eligible_zone_ids",
                "window_open",
                "active_zone_id",
                "resume_pending",
                "interrupted_reason",
                "interrupted_zone_id",
                "charging_limit_reached_at",
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
            if not self._eligible_zones():
                raise RuntimeError(
                    "Configure at least one successfully completed automatic mowing zone first"
                )
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
        self._legacy_selection_migration_allowed = False
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

    async def async_set_custom_queue(self, zone_ids: list[int]) -> None:
        """Persist a user-defined custom mowing queue without starting mowing."""
        queue = self._normalize_queue(zone_ids)
        selected = set(self._selected_zone_ids)
        if not queue:
            raise ValueError("Custom mowing queue may not be empty")
        unknown = [zone_id for zone_id in queue if zone_id not in selected]
        if unknown:
            raise ValueError(f"Queue contains zones outside the selected schedule allowlist: {unknown}")
        eligible = {int(row["id"]) for row in self._eligible_zones() if row.get("id") is not None}
        unproven = [zone_id for zone_id in queue if zone_id not in eligible]
        if unproven:
            raise ValueError(f"Queue contains zones without a confirmed completed mowing: {unproven}")
        self._custom_queue = queue
        self._order_mode = SCHEDULE_ORDER_CUSTOM
        self._runtime["completed_queue_slots"] = []
        self._runtime["active_queue_slot"] = None
        self._update_options(**{OPT_SCHEDULE_CUSTOM_QUEUE: list(queue), OPT_SCHEDULE_ORDER_MODE: SCHEDULE_ORDER_CUSTOM})
        await self._save()
        if self._enabled:
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

    def _maybe_migrate_legacy_zone_selection(self) -> None:
        """Preserve an already-enabled beta scheduler without auto-enrolling future zones."""
        if not self._legacy_selection_migration_allowed or self._selection_configured:
            return
        zones = self._zones()
        if not zones:
            return
        proven: set[int] = set()
        for row in zones:
            try:
                zone_id = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            if zone_id > 0 and parse_iso(row.get("last_completed_at")) is not None:
                proven.add(zone_id)
        self._selected_zone_ids = proven
        self._selection_configured = True
        self._legacy_selection_migration_allowed = False
        self._update_options(
            **{
                OPT_SCHEDULE_ZONE_IDS: [str(value) for value in sorted(proven)],
                OPT_SCHEDULE_MODE: self._mode,
            }
        )
        _LOGGER.info(
            "Migrated the enabled beta Navimower schedule to an explicit allowlist of %s proven zones",
            len(proven),
        )

    def _custom_queue_entries(self) -> list[dict[str, Any]]:
        eligible = {int(row["id"]): row for row in self._eligible_zones() if row.get("id") is not None}
        entries = []
        for slot, zone_id in enumerate(self._custom_queue):
            if zone_id in eligible and zone_id in self._selected_zone_ids:
                entries.append({"slot": slot, "zone_id": zone_id, "zone": eligible[zone_id]})
        return entries

    def _next_custom_entry(self) -> dict[str, Any] | None:
        completed = {int(v) for v in self._runtime.get("completed_queue_slots") or []}
        for entry in self._custom_queue_entries():
            if entry["slot"] not in completed:
                return entry
        return None

    def _eligible_zones(self) -> list[dict[str, Any]]:
        return filter_schedule_zones(
            self._zones(),
            self._selected_zone_ids,
            scheduler_completed_at=self._runtime.get("scheduler_completed_at") or {},
        )

    def _window_state(self, now: datetime) -> tuple[bool, str | None]:
        if self._mode == SCHEDULE_MODE_CONTINUOUS:
            return True, "continuous"
        return window_state(now, self._start, self._end)

    def _window_open_now(self) -> bool:
        """Re-read the clock immediately before any interrupted-task command."""
        if not self._enabled:
            return False
        open_now, _ = self._window_state(dt_util.now())
        return open_now

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

    @staticmethod
    def _vendor_mowing(data: dict[str, Any]) -> bool:
        """Return only a mower-confirmed cutting state, never optimistic HA activity."""
        mqtt_state = _as_int(data.get("mqtt_vehicle_state"))
        if mqtt_state is not None:
            return mqtt_state == MQTT_STATE_MOWING
        return str(data.get("state_code") or "") in {STATE_MOWING, STATE_MOWING_MANUAL}

    @staticmethod
    def _vendor_charging(data: dict[str, Any]) -> bool:
        """Return whether the mower itself currently reports charging in the dock."""
        mqtt_state = _as_int(data.get("mqtt_vehicle_state"))
        if mqtt_state is not None:
            return mqtt_state == MQTT_STATE_CHARGING
        return str(data.get("state_code") or "") == STATE_IDLE_DOCKED_POST

    @staticmethod
    def _charging_limit_percent(data: dict[str, Any]) -> int | None:
        """Return the mower/user charging limit used by the HA number entity."""
        value = _as_int((data.get("settings") or {}).get("charging_limit"))
        return value if value is not None and 1 <= value <= 100 else None

    def _clear_interruption_runtime(self) -> None:
        self._runtime["resume_pending"] = False
        self._runtime["interrupted_reason"] = None
        self._runtime["interrupted_zone_id"] = None
        self._runtime["interrupted_cycle_id"] = None
        self._runtime["progress_before_interrupt"] = None
        self._runtime["charging_limit_reached_at"] = None

    def _charging_interruption_confirmed(self) -> bool:
        """Return whether the notification state proved a low-battery charging pause."""
        center = getattr(self.coordinator, "notification_center", None)
        return getattr(center, "interrupted_reason", None) == "charging"

    async def _capture_charging_interruption(self) -> None:
        """Retain a mower-owned low-battery interruption without forcing Resume."""
        zone_id = self._runtime.get("active_zone_id")
        if zone_id is None or self._runtime.get("resume_pending"):
            return
        self._runtime["resume_pending"] = True
        self._runtime["interrupted_reason"] = "low_battery"
        self._runtime["interrupted_zone_id"] = int(zone_id)
        self._runtime["interrupted_cycle_id"] = self._runtime.get("active_cycle_id")
        self._runtime["progress_before_interrupt"] = self._progress_for_zone(int(zone_id))
        self._runtime["charging_limit_reached_at"] = None
        self._runtime["pending_command"] = None
        self._runtime["last_command"] = f"charging_pause:{zone_id}"
        self._runtime["last_command_at"] = _utc_now()
        self._runtime["last_error"] = None
        await self._save()

    async def _evaluate_low_battery_resume(self, data: dict[str, Any]) -> None:
        """Let the mower self-resume; use charging limit plus grace as fallback."""
        zone_id = self._runtime.get("interrupted_zone_id") or self._runtime.get("active_zone_id")
        if zone_id is None:
            self._clear_interruption_runtime()
            await self._save()
            return

        battery = _as_int(data.get("battery"))
        limit = self._charging_limit_percent(data)
        reached_at = self._runtime.get("charging_limit_reached_at")
        if battery is None or limit is None or battery < limit:
            if reached_at is not None:
                self._runtime["charging_limit_reached_at"] = None
                await self._save()
            return

        if reached_at is None:
            reached_at = _utc_now()
            self._runtime["charging_limit_reached_at"] = reached_at
            self._runtime["last_command"] = f"charging_limit_reached:{zone_id}:{battery}/{limit}"
            self._runtime["last_command_at"] = reached_at
            self._runtime["last_error"] = None
            await self._save()
            return

        age = _age_seconds(reached_at)
        if age is None or age < _LOW_BATTERY_RESUME_GRACE_SECONDS:
            return

        # Re-read live data and the wall clock immediately before fallback.
        fresh_data = self.coordinator.data or {}
        if self._vendor_mowing(fresh_data):
            self._clear_interruption_runtime()
            self._runtime["pending_command"] = None
            self._runtime["last_command"] = "retained_task_already_mowing"
            self._runtime["last_command_at"] = _utc_now()
            self._runtime["last_error"] = None
            await self._save()
            return

        fresh_battery = _as_int(fresh_data.get("battery"))
        fresh_limit = self._charging_limit_percent(fresh_data)
        if fresh_battery is None or fresh_limit is None or fresh_battery < fresh_limit:
            self._runtime["charging_limit_reached_at"] = None
            await self._save()
            return
        if fresh_data.get("error") is True or fresh_data.get("problem_latched") is True:
            return
        if not self._window_open_now():
            return

        await self._continue_interrupted_task(
            source="navimower_schedule_charge_limit_fallback",
            continue_source="navimower_schedule_charge_limit_continue_fallback",
        )

    def _pending_mow_confirmed(self, pending: dict[str, Any], data: dict[str, Any]) -> bool:
        """Confirm a scheduler start from vendor state and the commanded target."""
        if not self._vendor_mowing(data):
            return False
        zone_id = _as_int(pending.get("zone_id"))
        if zone_id is None:
            return False
        observed_zone = _as_int(data.get("active_zone_progress_zone_id"))
        if observed_zone == zone_id:
            return True
        return pending.get("vendor_mowing_at_send") is False

    def _sync_active_cycle_id(self) -> bool:
        """Attach the newly-created history cycle once cutting actually starts."""
        if (
            self._runtime.get("active_zone_id") is None
            or self._runtime.get("active_cycle_id") is not None
        ):
            return False
        history = getattr(self.coordinator, "history", None)
        active = getattr(history, "active_session", None)
        if not isinstance(active, dict) or not active.get("id"):
            return False
        try:
            zone_id = int(self._runtime["active_zone_id"])
        except (TypeError, ValueError):
            return False
        observed: set[int] = set()
        for value in [
            *(active.get("zone_ids") or []),
            *(active.get("cycle_reset_zone_ids") or []),
        ]:
            try:
                observed.add(int(value))
            except (TypeError, ValueError):
                continue
        if zone_id not in observed:
            return False
        self._runtime["active_cycle_id"] = str(active["id"])
        return True

    async def _reconcile_unconfirmed_mow_start(self) -> None:
        """Stop safely when a scheduler start was never confirmed by the mower."""
        pending = self._runtime.get("pending_command")
        if not isinstance(pending, dict) or pending.get("kind") != "mow":
            return
        age = self._pending_age()
        if age is None or age < _MOW_CONFIRM_SECONDS:
            return
        zone_id = pending.get("zone_id")
        self.coordinator.clear_pending_activity()
        self.coordinator.clear_command_target()
        self._runtime["pending_command"] = None
        self._runtime["suspended_reason"] = "mow_start_not_confirmed"
        self._runtime["last_error"] = (
            "New-zone mowing start was not confirmed by the mower; automatic reset retry was refused"
        )
        self._runtime["last_command"] = f"mow_start_unconfirmed:{zone_id}"
        self._runtime["last_command_at"] = _utc_now()
        await self._save()

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
        self._maybe_migrate_legacy_zone_selection()
        settings = data.get("settings") or {}
        if settings.get("schedule_enabled") is True:
            await self.async_set_enabled(False, reason="native_schedule_enabled")
            return

        now = dt_util.now()
        in_window, token = self._window_state(now)
        if in_window and token and token != self._runtime.get("window_token"):
            self._runtime["window_token"] = token
            self._runtime["round_index"] = 1
            self._runtime["round_started_at"] = _utc_now()
            self._runtime["completed_zone_ids_in_window"] = []
            self._runtime["completed_queue_slots"] = []
            self._runtime["active_queue_slot"] = None
            self._runtime["just_completed_zone_id"] = None
            self._runtime["suspended_reason"] = None
            self._runtime["retry_not_before"] = None
            await self._save()

        completed_now = await self._confirm_active_completion()
        activity = data.get("activity")
        await self._confirm_pending(data, activity)
        if self._sync_active_cycle_id():
            await self._save()
        await self._reconcile_unconfirmed_mow_start()

        if not in_window:
            await self._enforce_closed_window(data, activity)
            return
        if self._runtime.get("suspended_reason"):
            return

        # Upgrade a beta40-beta46 persisted low-battery interruption safely.
        if (
            self._runtime.get("resume_pending")
            and self._runtime.get("interrupted_reason") is None
            and self._charging_interruption_confirmed()
        ):
            self._runtime["interrupted_reason"] = "low_battery"
            self._runtime["charging_limit_reached_at"] = None
            await self._save()

        if (
            self._runtime.get("active_zone_id") is not None
            and not self._runtime.get("resume_pending")
            and not self._vendor_mowing(data)
            and self._charging_interruption_confirmed()
        ):
            await self._capture_charging_interruption()

        if self._runtime.get("resume_pending"):
            if self._vendor_mowing(data):
                self._clear_interruption_runtime()
                self._runtime["pending_command"] = None
                self._runtime["last_command"] = "retained_task_already_mowing"
                self._runtime["last_command_at"] = _utc_now()
                self._runtime["last_error"] = None
                await self._save()
                return
            if self._runtime.get("interrupted_reason") == "low_battery":
                await self._evaluate_low_battery_resume(data)
                return
            await self._continue_interrupted_task(
                source="navimower_schedule_window_resume",
                continue_source="navimower_schedule_window_continue_fallback",
            )
            return

        if self._runtime.get("active_zone_id") is not None:
            return

        pending = self._runtime.get("pending_command")
        if isinstance(pending, dict):
            return
        if not self._retry_ready():
            return
        if self._vendor_charging(data):
            return
        direct_handoff = (
            completed_now
            and activity in {ACTIVITY_MOWING, ACTIVITY_RETURNING}
            and not self._charging_interruption_confirmed()
        )
        if activity not in {ACTIVITY_DOCKED, ACTIVITY_PAUSED} and not direct_handoff:
            return

        completed = {int(value) for value in self._runtime.get("completed_zone_ids_in_window") or []}
        eligible = self._eligible_zones()
        custom_entry = self._next_custom_entry() if self._order_mode == SCHEDULE_ORDER_CUSTOM else None
        candidate = custom_entry["zone"] if custom_entry is not None else (None if self._order_mode == SCHEDULE_ORDER_CUSTOM else select_oldest_zone(
            eligible,
            completed_in_window=completed,
            just_completed_zone_id=self._runtime.get("just_completed_zone_id"),
            scheduler_completed_at=self._runtime.get("scheduler_completed_at") or {},
        ))
        if candidate is None and self._mode == SCHEDULE_MODE_CONTINUOUS and eligible:
            if self._order_mode == SCHEDULE_ORDER_CUSTOM and self._custom_queue_entries():
                self._runtime["completed_queue_slots"] = []
                self._runtime["completed_zone_ids_in_window"] = []
                self._runtime["just_completed_zone_id"] = None
                self._runtime["round_index"] = int(self._runtime.get("round_index") or 1) + 1
                self._runtime["round_started_at"] = _utc_now()
                await self._save()
                custom_entry = self._next_custom_entry()
                candidate = custom_entry["zone"] if custom_entry else None
            else:
                eligible_ids = {int(row["id"]) for row in eligible if row.get("id") is not None}
                if eligible_ids and eligible_ids.issubset(completed):
                    self._runtime["completed_zone_ids_in_window"] = []
                    self._runtime["just_completed_zone_id"] = None
                    self._runtime["round_index"] = int(self._runtime.get("round_index") or 1) + 1
                    self._runtime["round_started_at"] = _utc_now()
                    await self._save()
                    candidate = select_oldest_zone(eligible, completed_in_window=set(), just_completed_zone_id=None, scheduler_completed_at=self._runtime.get("scheduler_completed_at") or {})
        if candidate is None:
            return
        try:
            zone_id = int(candidate["id"])
        except (KeyError, TypeError, ValueError):
            return
        queue_slot = custom_entry["slot"] if self._order_mode == SCHEDULE_ORDER_CUSTOM and custom_entry else None
        await self._async_send_mow(zone_id, reset=True, source="navimower_schedule_next_zone", queue_slot=queue_slot)

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
        active_slot = self._runtime.get("active_queue_slot")
        if active_slot is not None:
            slots = {int(v) for v in self._runtime.get("completed_queue_slots") or []}
            slots.add(int(active_slot))
            self._runtime["completed_queue_slots"] = sorted(slots)
        self._runtime["active_queue_slot"] = None
        self._runtime["just_completed_zone_id"] = int(zone_id)
        confirmed = dict(self._runtime.get("scheduler_completed_at") or {})
        confirmed[str(zone_id)] = later_iso(confirmed.get(str(zone_id)), stamp) or stamp
        self._runtime["scheduler_completed_at"] = confirmed
        self._runtime["active_zone_id"] = None
        self._runtime["active_cycle_id"] = None
        self._runtime["active_zone_baseline_completed_at"] = None
        self._runtime["dispatch_started_at"] = None
        self._clear_interruption_runtime()
        self._runtime["pending_command"] = None
        self._runtime["retry_not_before"] = None
        self._runtime["last_command"] = f"zone_completed:{zone_id}"
        self._runtime["last_command_at"] = _utc_now()
        self._runtime["last_error"] = None
        await self._save()
        return True

    async def _confirm_pending(self, data: dict[str, Any], activity: Any) -> None:
        pending = self._runtime.get("pending_command")
        if not isinstance(pending, dict):
            return
        kind = str(pending.get("kind") or "")
        if kind == "mow":
            if not self._pending_mow_confirmed(pending, data):
                return
            zone_id = _as_int(pending.get("zone_id"))
            if zone_id is None:
                return
            baseline = pending.get("baseline_completed_at")
            sent_at = str(pending.get("sent_at") or _utc_now())
            source = str(pending.get("source") or "navimower_schedule_next_zone")
            self.coordinator.start_new_mowing_cycle([zone_id], source=source)
            self._runtime["active_zone_id"] = zone_id
            self._runtime["active_queue_slot"] = pending.get("queue_slot")
            self._runtime["active_cycle_id"] = None
            self._runtime["active_zone_baseline_completed_at"] = baseline
            self._runtime["dispatch_started_at"] = sent_at
            self._runtime["just_completed_zone_id"] = None
            self._runtime["retry_not_before"] = None
            self._runtime["pending_command"] = None
            self._runtime["last_error"] = None
            await self._save()
            return
        if kind in {"resume", "continue"} and self._vendor_mowing(data):
            self._runtime["pending_command"] = None
            self._clear_interruption_runtime()
            self._runtime["last_error"] = None
            await self._save()
        elif kind == "dock" and activity in {ACTIVITY_RETURNING, ACTIVITY_DOCKED}:
            self._runtime["pending_command"] = None
            await self._save()

    async def _enforce_closed_window(self, data: dict[str, Any], activity: Any) -> None:
        zone_id = self._runtime.get("active_zone_id")
        changed = False
        if zone_id is not None and not self._runtime.get("resume_pending"):
            self._runtime["resume_pending"] = True
            self._runtime["interrupted_reason"] = "window_closed"
            self._runtime["interrupted_zone_id"] = int(zone_id)
            self._runtime["interrupted_cycle_id"] = self._runtime.get("active_cycle_id")
            self._runtime["progress_before_interrupt"] = self._progress_for_zone(int(zone_id))
            self._runtime["charging_limit_reached_at"] = None
            changed = True
        elif self._runtime.get("resume_pending") and self._runtime.get("interrupted_reason") is None:
            self._runtime["interrupted_reason"] = (
                "low_battery" if self._charging_interruption_confirmed() else "window_closed"
            )
            changed = True

        pending = self._runtime.get("pending_command")
        if isinstance(pending, dict) and pending.get("kind") in {"resume", "continue"}:
            self._runtime["pending_command"] = None
            changed = True
        if changed:
            await self._save()

        if not self._vendor_mowing(data) and activity != ACTIVITY_PAUSED:
            return
        pending = self._runtime.get("pending_command")
        age = self._pending_age()
        if isinstance(pending, dict) and pending.get("kind") == "dock" and age is not None and age < _DOCK_RETRY_SECONDS:
            return
        await self._async_send_dock("navimower_schedule_window_closed")

    async def _continue_interrupted_task(self, *, source: str, continue_source: str) -> None:
        zone_id = self._runtime.get("interrupted_zone_id") or self._runtime.get("active_zone_id")
        if zone_id is None:
            self._clear_interruption_runtime()
            await self._save()
            return

        fresh_data = self.coordinator.data or {}
        if self._vendor_mowing(fresh_data):
            self._clear_interruption_runtime()
            self._runtime["pending_command"] = None
            self._runtime["last_command"] = "retained_task_already_mowing"
            self._runtime["last_command_at"] = _utc_now()
            self._runtime["last_error"] = None
            await self._save()
            return
        if fresh_data.get("error") is True or fresh_data.get("problem_latched") is True:
            return

        pending = self._runtime.get("pending_command")
        age = self._pending_age()
        if isinstance(pending, dict):
            kind = pending.get("kind")
            if kind == "resume" and age is not None and age >= _RESUME_CONFIRM_SECONDS:
                if not self._window_open_now():
                    return
                self._runtime["pending_command"] = None
                await self._async_send_mow(int(zone_id), reset=False, source=continue_source)
            elif kind == "continue" and age is not None and age >= _CONTINUE_CONFIRM_SECONDS:
                self._runtime["pending_command"] = None
                self._runtime["suspended_reason"] = "interrupted_task_continue_not_confirmed"
                self._runtime["last_error"] = "Resume and one-zone continue were not confirmed; automatic reset was refused"
                await self._save()
            return

        # This second clock read is intentional: a window can close between the
        # scheduler tick and the actual command path, especially after a grace wait.
        if not self._window_open_now():
            return
        try:
            await async_resume_task(self.coordinator, source=source)
        except Exception as err:
            self._runtime["last_error"] = f"Resume failed: {type(err).__name__}: {err}"
            if not self._window_open_now():
                await self._save()
                return
            await self._async_send_mow(int(zone_id), reset=False, source=continue_source)
            return
        self._runtime["pending_command"] = {
            "kind": "resume",
            "zone_id": int(zone_id),
            "sent_at": _utc_now(),
            "source": source,
        }
        self._runtime["last_command"] = f"resume:{zone_id}"
        self._runtime["last_command_at"] = _utc_now()
        await self._save()

    async def _async_send_mow(self, zone_id: int, *, reset: bool, source: str, queue_slot: int | None = None) -> None:
        row = self._zone(zone_id) or {}
        partition_ids = encode_partition_ids([zone_id])
        partition_setup = mow_setup(reset=reset, ordered=False)
        data_before_send = self.coordinator.data or {}
        vendor_mowing_at_send = self._vendor_mowing(data_before_send)
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

        if not reset:
            self._runtime["active_zone_id"] = int(self._runtime.get("active_zone_id") or zone_id)
            self._runtime["resume_pending"] = True
        self._runtime["pending_command"] = {
            "kind": "mow" if reset else "continue",
            "zone_id": zone_id,
            "queue_slot": queue_slot,
            "reset": reset,
            "sent_at": sent_at,
            "source": source,
            "baseline_completed_at": row.get("last_completed_at") if reset else None,
            "vendor_mowing_at_send": vendor_mowing_at_send if reset else None,
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
