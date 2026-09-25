"""Home Assistant Activity/Logbook context for Navimower state changes."""
from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from homeassistant.core import Context, HomeAssistant, callback

from .const import DOMAIN

EVENT_NAVIMOWER_ACTIVITY = "navimower_activity"
_COMMAND_TTL_SECONDS = 180.0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _human_source(source: str) -> str:
    raw = _text(source)
    if raw.startswith("navimower_schedule"):
        return "Navimower Schedule"
    if raw == "navimower.mow":
        return "Navimower Mow action"
    if raw in {"navimower.resume", "navimower.continue_task"}:
        return "Navimower Resume action"
    if raw == "navimower.continue_last_ordered_run":
        return "Navimower ordered-task continuation"
    if raw.startswith("lawn_mower.start_mowing"):
        return "Home Assistant Start mowing"
    if raw == "lawn_mower.dock":
        return "Home Assistant Dock"
    if raw == "lawn_mower.pause":
        return "Home Assistant Pause"
    return raw.replace("_", " ") if raw else "Navimower"


def _zone_phrase(value: Any) -> str:
    text = _text(value)
    return text if text else "unknown"


class NavimowerActivityContextManager:
    """Create per-entity contexts before coordinator entities write new states."""

    def __init__(self, hass: HomeAssistant, entry_id: str, coordinator: Any) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.coordinator = coordinator
        self._unsub = None
        self._previous: dict[str, Any] = {}
        self._contexts: dict[str, Context] = {}
        self._pending_command: dict[str, Any] | None = None

    def start(self) -> None:
        """Start before coordinator entities are registered."""
        if self._unsub is not None:
            return
        self._previous = self._capture(self.coordinator.data or {})
        self._unsub = self.coordinator.async_add_listener(self._handle_update)

    async def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._contexts = {}
        self._pending_command = None

    def context_for(self, key: str) -> Context | None:
        """Return the cause context for this entity in the current update."""
        return self._contexts.get(str(key))

    def note_command(
        self,
        *,
        source: str,
        message: str,
        context: Context | None = None,
    ) -> None:
        """Remember a fresh HA/integration command until its optimistic state lands."""
        self._pending_command = {
            "source": str(source),
            "message": str(message),
            "context": context,
            "set_at": time.monotonic(),
        }

    def clear_command(self) -> None:
        self._pending_command = None

    def _fresh_command(self) -> dict[str, Any] | None:
        row = self._pending_command
        if not isinstance(row, dict):
            return None
        stamp = row.get("set_at")
        if not isinstance(stamp, (int, float)):
            return None
        if not 0 <= time.monotonic() - stamp <= _COMMAND_TTL_SECONDS:
            self._pending_command = None
            return None
        return row

    @staticmethod
    def _capture(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "activity": data.get("activity"),
            "state": data.get("state"),
            "current_zone": data.get("current_zone"),
            "current_physical_zone": data.get("current_physical_zone"),
            "target_zone": data.get("target_zone"),
            "planned_zones": data.get("planned_zones"),
            "pause_reason": data.get("mowing_pause_reason"),
            "weather_hold_reason": data.get("weather_hold_reason"),
        }

    @callback
    def _handle_update(self) -> None:
        data = self.coordinator.data or {}
        current = self._capture(data)
        previous = self._previous
        self._previous = deepcopy(current)
        # Contexts are deliberately one coordinator update wide. NavimowEntity
        # clears stale entity context on every later update.
        self._contexts = {}
        if not previous:
            return

        if current["activity"] != previous.get("activity"):
            message, source = self._activity_reason(
                previous.get("activity"),
                current["activity"],
                data,
            )
            self._emit(
                "Mower activity",
                message,
                source=source,
                keys={"mower", "status", "mowing_pause_reason"},
                parent=self._command_parent_if_matching(),
            )
            self._pending_command = None

        if current["current_zone"] != previous.get("current_zone"):
            old = _zone_phrase(previous.get("current_zone"))
            new = _zone_phrase(current["current_zone"])
            source = _human_source(self._recent_task_source())
            message = f"Vendor task zone selection changed from {old} to {new}."
            if source != "Navimower":
                message += f" Latest task source: {source}."
            self._emit(
                "Vendor task zones",
                message,
                source=source,
                keys={"current_zone"},
            )

        if current["current_physical_zone"] != previous.get("current_physical_zone"):
            old = _zone_phrase(previous.get("current_physical_zone"))
            new = _zone_phrase(current["current_physical_zone"])
            message = f"Mower position moved from {old} to {new}."
            self._emit(
                "Physical zone",
                message,
                source=_text(data.get("current_physical_zone_source")) or "Position",
                keys={"current_physical_zone"},
            )

        if current["target_zone"] != previous.get("target_zone"):
            old = _zone_phrase(previous.get("target_zone"))
            new = _zone_phrase(current["target_zone"])
            source = _text(data.get("target_zone_immediate_source")) or "target resolver"
            if new == "No active target":
                message = f"Immediate mowing target was cleared from {old}."
                activity = _text(data.get("activity"))
                if activity:
                    message += f" Mower activity is {activity}."
            elif old == "No active target" or old == "unknown":
                message = f"Immediate mowing target became {new}."
            else:
                message = f"Immediate mowing target changed from {old} to {new}."
            self._emit(
                "Target zone",
                message,
                source=source,
                keys={"target_zone"},
            )

        if current["planned_zones"] != previous.get("planned_zones"):
            old = _zone_phrase(previous.get("planned_zones"))
            new = _zone_phrase(current["planned_zones"])
            source = _text(data.get("planned_zones_source")) or "task resolver"
            if new == "No planned zones":
                message = f"Active mowing task zones were cleared from {old}."
            elif old == "No planned zones" or old == "unknown":
                message = f"Active mowing task planned zones became {new}."
            else:
                message = f"Active mowing task planned zones changed from {old} to {new}."
            self._emit(
                "Planned zones",
                message,
                source=source,
                keys={"planned_zones"},
            )

    def _command_parent_if_matching(self) -> Context | None:
        row = self._fresh_command()
        context = (row or {}).get("context")
        return context if isinstance(context, Context) else None

    def _recent_task_source(self) -> str:
        command = self._fresh_command()
        if command:
            return _text(command.get("source"))
        trace = getattr(self.coordinator, "_last_mow_command_trace", None)
        if isinstance(trace, dict) and not trace.get("send_error"):
            stamp = trace.get("_started_monotonic")
            if isinstance(stamp, (int, float)) and 0 <= time.monotonic() - stamp <= 180:
                return _text(trace.get("source"))
        resume = getattr(self.coordinator, "_last_resume_command", None)
        if isinstance(resume, dict) and not resume.get("error"):
            return _text(resume.get("source"))
        return ""

    def _activity_reason(
        self,
        previous: Any,
        current: Any,
        data: dict[str, Any],
    ) -> tuple[str, str]:
        old = _text(previous) or "unknown"
        new = _text(current) or "unknown"
        command = self._fresh_command()
        if command is not None:
            source = _human_source(_text(command.get("source")))
            return str(command.get("message") or f"{source} changed mower activity to {new}."), source

        source_raw = self._recent_task_source()
        source = _human_source(source_raw)
        if new == "mowing" and source_raw:
            zones = _text(data.get("planned_zones"))
            suffix = (
                f" Planned zones: {zones}."
                if zones and zones != "No planned zones"
                else ""
            )
            return f"{source} started or resumed mowing.{suffix}", source

        if new == "paused":
            reason = (
                _text(data.get("mowing_pause_reason"))
                or _text(data.get("weather_hold_reason"))
            )
            if reason:
                return f"Mowing paused because {reason}.", "Navimow"
            return f"Mower activity changed from {old} to paused.", "Navimow"

        if new == "returning":
            weather = (
                _text(data.get("mowing_pause_reason"))
                or _text(data.get("weather_hold_reason"))
            )
            if weather and weather not in {"none", "unknown"}:
                return f"Mower started returning because {weather}.", "Navimow"
            battery = data.get("battery")
            threshold = (data.get("settings") or {}).get("return_battery_level")
            try:
                if battery is not None and threshold is not None and float(battery) <= float(threshold) + 2:
                    return (
                        f"Mower started returning near the configured battery return level "
                        f"({battery}% / {threshold}%).",
                        "Navimow",
                    )
            except (TypeError, ValueError):
                pass
            return "Mower started returning to the dock.", "Navimow"

        if new == "docked":
            if old == "returning":
                return "Mower reached the dock after returning.", "Navimow"
            return f"Mower activity changed from {old} to docked.", "Navimow"

        return f"Mower activity changed from {old} to {new}.", "Navimow"

    def _emit(
        self,
        name: str,
        message: str,
        *,
        source: str,
        keys: set[str],
        parent: Context | None = None,
    ) -> None:
        context = Context(
            parent_id=parent.id if parent is not None else None,
            user_id=parent.user_id if parent is not None else None,
        )
        self.hass.bus.async_fire(
            EVENT_NAVIMOWER_ACTIVITY,
            {
                "name": str(name),
                "message": str(message),
                "source": str(source),
                "entry_id": self.entry_id,
                "mower_name": _text((self.coordinator.data or {}).get("name")) or "Navimow",
            },
            context=context,
        )
        for key in keys:
            self._contexts[str(key)] = context
