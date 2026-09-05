"""Cancel stale gate intent when a fresh local command keeps the mower in-zone.

A gate transition latch intentionally survives brief vendor target flips while a
mower is travelling between zones. That safety becomes harmful when a new,
explicit one-zone Home Assistant command starts in the mower's *current* zone:
an older opposite-zone target can otherwise leave the previous latch asserted
for the rest of the mowing task.

This layer treats that explicit same-zone command as authoritative. It clears
any conflicting old latch immediately and keeps a short-lived command guard
until the vendor target confirms the same zone. During that hand-over a stale
vendor target is display/debug evidence only and cannot re-arm the physical gate.
"""
from __future__ import annotations

import time
from typing import Any

from . import coordinator as _coordinator


_GUARD_SOURCE = "same_zone_command_guard"
_HA_TARGET_SOURCES = {"ha_command", "ha_command_confirmed"}
_SAME_ZONE_COMMAND_GATE_GUARD_SECONDS = 120.0


def _as_int(value: Any) -> int | None:
    """Return an integer value without leaking vendor type quirks."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _single_zone(values: Any) -> int | None:
    """Return the sole positive zone id, otherwise ``None``."""
    parsed: list[int] = []
    for value in values or []:
        zone_id = _as_int(value)
        if zone_id is not None and zone_id > 0 and zone_id not in parsed:
            parsed.append(zone_id)
    return parsed[0] if len(parsed) == 1 else None


def _latch_conflicts_with_zone(latch: Any, zone_id: Any) -> bool:
    """Return whether ``latch`` leaves ``zone_id`` for a different zone."""
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
    )


def _clear_conflicting_latches(coordinator: Any, zone_id: int) -> list[str]:
    """Drop latches that contradict an authoritative same-zone command."""
    removed: list[str] = []
    for slug, latch in list(coordinator._gate_latches.items()):  # noqa: SLF001
        if not _latch_conflicts_with_zone(latch, zone_id):
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
    """Keep the current navigation snapshot consistent after latch removal."""
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


def install_gate_intent_safety() -> None:
    """Install same-zone command arbitration once per interpreter."""
    cls = _coordinator.NavimowCoordinator
    if getattr(cls, "_gate_intent_safety_installed", False):
        return

    original_set_command_target = cls.set_command_target
    original_navigation = cls._navigation_fields

    def set_command_target(
        self: Any,
        zone_ids: list[int],
        *,
        source: str = "ha_mow_command",
    ) -> None:
        ids = _coordinator._dedupe_zone_ids(zone_ids)  # noqa: SLF001
        command_zone = _single_zone(ids)
        current_zone = _as_int(
            (self.data or {}).get("current_physical_zone_id")
        )

        if command_zone is not None and current_zone == command_zone:
            removed = _clear_conflicting_latches(self, command_zone)
            self._same_zone_command_gate_guard = {  # noqa: SLF001
                "zone_id": command_zone,
                "started_at": time.monotonic(),
                "source": str(source),
                "cleared_latches": removed,
            }
        else:
            # A new command for another zone is positive replacement intent and
            # must immediately release any previous same-zone suppression.
            self._same_zone_command_gate_guard = None  # noqa: SLF001

        original_set_command_target(self, zone_ids, source=source)

    def navigation_fields(
        self: Any, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        result = original_navigation(self, snapshot)
        guard = getattr(self, "_same_zone_command_gate_guard", None)
        if not isinstance(guard, dict):
            return result

        zone_id = _as_int(guard.get("zone_id"))
        started_at = guard.get("started_at")
        try:
            age = time.monotonic() - float(started_at)
        except (TypeError, ValueError):
            age = _SAME_ZONE_COMMAND_GATE_GUARD_SECONDS + 1

        if (
            zone_id is None
            or age < 0
            or age > _SAME_ZONE_COMMAND_GATE_GUARD_SECONDS
        ):
            self._same_zone_command_gate_guard = None  # noqa: SLF001
            return result

        current_zone = _as_int(result.get("current_physical_zone_id"))
        if current_zone is not None and current_zone != zone_id:
            self._same_zone_command_gate_guard = None  # noqa: SLF001
            return result

        target_ids = _coordinator._dedupe_zone_ids(  # noqa: SLF001
            result.get("target_zone_ids")
        )
        target_source = str(result.get("target_zone_source") or "")

        # Returning-to-dock and any later explicit command are real replacement
        # intents. ``set_command_target`` already clears the guard for the latter;
        # the returning check protects mower-owned return transitions as well.
        if target_source == "returning_to_dock":
            self._same_zone_command_gate_guard = None  # noqa: SLF001
            return result

        # Once a non-HA source reports the commanded current zone, vendor state
        # has caught up. Clear the old latch one final time, publish a consistent
        # OFF snapshot, then retire the guard.
        if target_ids == [zone_id] and target_source not in _HA_TARGET_SOURCES:
            removed = _clear_conflicting_latches(self, zone_id)
            if removed:
                self._last_target_zone_ids = [zone_id]  # noqa: SLF001
                _clear_gate_state_rows(
                    result,
                    removed,
                    zone_id=zone_id,
                    reason="vendor_confirmed_same_zone_command",
                )
                if _no_gate_required(result):
                    result["zone_transition"] = False
            self._same_zone_command_gate_guard = None  # noqa: SLF001
            return result

        # The local one-zone command still owns intent during vendor hand-over.
        # A contradictory non-HA target may be an old packet (the field failure
        # that created a 36->37 latch while Schedule had just dispatched 36).
        # Remove any latch original navigation just re-created and expose the
        # authoritative same-zone target until vendor state converges.
        if (
            target_ids
            and target_ids != [zone_id]
            and target_source not in _HA_TARGET_SOURCES
        ):
            removed = _clear_conflicting_latches(self, zone_id)
            self._last_target_zone_ids = [zone_id]  # noqa: SLF001
            stale_target_ids = list(target_ids)
            result["target_zone_ids"] = [zone_id]
            current_name = str(result.get("current_physical_zone") or "").strip()
            if current_name and current_name not in {
                "Between zones",
                "Outside mapped zones",
                "Position unavailable",
            }:
                result["target_zone"] = current_name
            result["target_zone_source"] = _GUARD_SOURCE
            result["same_zone_command_guard"] = {
                "active": True,
                "zone_id": zone_id,
                "age_seconds": round(age, 1),
                "command_source": guard.get("source"),
                "suppressed_target_zone_ids": stale_target_ids,
            }
            _clear_gate_state_rows(
                result,
                removed,
                zone_id=zone_id,
                reason="stale_vendor_target_after_same_zone_command",
            )
            if _no_gate_required(result):
                result["zone_transition"] = False

        return result

    cls.set_command_target = set_command_target
    cls._navigation_fields = navigation_fields
    cls._gate_intent_safety_installed = True
