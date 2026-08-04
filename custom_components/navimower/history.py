"""Persistent multi-session mowing trail and per-zone history.

The integration, not the browser, owns mowing sessions. Every valid local X/Y
sample is retained with timestamp, heading, activity and observed MQTT
state/action, except an exactly duplicated delivery. Active sessions are
checkpointed at a bounded interval while
completed sessions are stored as immutable Home Assistant Store files.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import logging
import threading
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_INCLUDE_RETURN_TRAIL,
    DEFAULT_TRAIL_RETENTION_DAYS,
    DOMAIN,
    MQTT_HISTORY_SAVE_DELAY_SECONDS,
    SESSION_CACHE_LIMIT,
    SESSION_MERGE_GAP_SECONDS,
    VENDOR_COMPLETION_PROGRESS_MIN,
)
from .zone_state import build_daily_trails, simplify_xy_points

_LOGGER = logging.getLogger(__name__)

_INDEX_VERSION = 1
_SESSION_VERSION = 1
_LEGACY_TRAIL_VERSION = 1

SESSION_DETAIL_POINT_FORMAT = [
    "timestamp_ms",
    "x",
    "y",
    "heading_radians",
    "activity",
    "mqtt_vehicle_state",
    "mqtt_action",
    "zone_id",
]
SESSION_CARD_POINT_FORMAT = ["x", "y"]


def _make_store(hass: HomeAssistant, version: int, key: str) -> Store:
    """Create a Store and serialize larger history outside the event loop."""
    try:
        return Store(hass, version, key, serialize_in_event_loop=False)
    except TypeError:  # Compatibility with older supported HA releases.
        return Store(hass, version, key)


def legacy_trail_store(hass: HomeAssistant, entry_id: str) -> Store:
    """Return the v0.1.x single-trail Store for migration/removal."""
    return _make_store(hass, _LEGACY_TRAIL_VERSION, f"{DOMAIN}_trail_{entry_id}")


def _index_store(hass: HomeAssistant, entry_id: str) -> Store:
    return _make_store(hass, _INDEX_VERSION, f"{DOMAIN}_sessions_{entry_id}")


def _session_store(hass: HomeAssistant, entry_id: str, session_id: str) -> Store:
    safe = "".join(ch for ch in str(session_id) if ch.isalnum() or ch in "_-")
    return _make_store(hass, _SESSION_VERSION, f"{DOMAIN}_session_{entry_id}_{safe}")


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



def _unique_ints(*groups: Any) -> list[int]:
    """Return unique integer values while preserving their first-seen order."""
    values: list[int] = []
    for group in groups:
        for raw in group or []:
            parsed = _as_int(raw)
            if parsed is not None and parsed not in values:
                values.append(parsed)
    return values


def _timestamp_ms(value: Any = None) -> int:
    """Normalize seconds/milliseconds to Unix milliseconds."""
    parsed = _as_int(value)
    if parsed is None or parsed <= 0:
        return int(time.time() * 1000)
    if parsed < 10_000_000_000:
        return parsed * 1000
    return parsed


def _iso(ms: int | None) -> str | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _metadata(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": session.get("id"),
        "sequence": session.get("sequence"),
        "started_at": session.get("started_at"),
        "started_at_ms": session.get("started_at_ms"),
        "ended_at": session.get("ended_at"),
        "ended_at_ms": session.get("ended_at_ms"),
        "active": bool(session.get("active")),
        "legacy": bool(session.get("legacy")),
        "approximate_timestamps": bool(session.get("approximate_timestamps")),
        "mode": session.get("mode"),
        "zone_ids": list(session.get("zone_ids") or []),
        "cutting_height_mm": session.get("cutting_height_mm"),
        "completed": session.get("completed"),
        "completion_reason": session.get("completion_reason"),
        "final_progress": deepcopy(session.get("final_progress") or {}),
        "cycle_reset_zone_ids": _unique_ints(session.get("cycle_reset_zone_ids")),
        "segment_count": max(1, len(session.get("segment_starts_ms") or [])),
        "point_count": (
            len(session.get("points") or [])
            if isinstance(session.get("points"), list)
            else (_as_int(session.get("point_count")) or 0)
        ),
    }


def _card_points(session: dict[str, Any]) -> list[list[float]]:
    return simplify_xy_points(
        [
            [float(point[1]), float(point[2])]
            for point in session.get("points") or []
            if isinstance(point, list) and len(point) >= 3
        ]
    )


def _card_segments(session: dict[str, Any]) -> list[list[list[float]]]:
    """Return route points split at every retained session-fragment boundary."""
    raw_points = [
        point
        for point in session.get("points") or []
        if isinstance(point, list) and len(point) >= 3
    ]
    if not raw_points:
        return []

    starts = sorted(
        dict.fromkeys(
            stamp
            for stamp in (
                _as_int(value)
                for value in (
                    session.get("segment_starts_ms")
                    or [session.get("started_at_ms")]
                )
            )
            if stamp is not None
        )
    )
    if len(starts) <= 1:
        points = _card_points(session)
        return [points] if points else []

    segments: list[list[list[float]]] = []
    current: list[list[float]] = []
    next_start_index = 1
    for point in raw_points:
        stamp = _as_int(point[0])
        while (
            stamp is not None
            and next_start_index < len(starts)
            and stamp >= starts[next_start_index]
        ):
            if current:
                segments.append(simplify_xy_points(current))
                current = []
            next_start_index += 1
        current.append([float(point[1]), float(point[2])])
    if current:
        segments.append(simplify_xy_points(current))
    return segments


def _card_session(session: dict[str, Any], *, include_points: bool) -> dict[str, Any]:
    """Return the stable payload consumed by the standalone map card."""
    row = _metadata(session)
    if include_points:
        # ``points`` remains for older cards. New cards should prefer
        # ``segments`` so a reload/pause gap is not bridged by a false line.
        row["points"] = _card_points(session)
        row["segments"] = _card_segments(session)
    return row


_SESSION_MERGE_GAP_MS = SESSION_MERGE_GAP_SECONDS * 1000
_SESSION_CLOCK_SKEW_MS = 30_000


def _session_end_ms(session: dict[str, Any]) -> int | None:
    """Return a reliable logical end timestamp for merge decisions."""
    ended = _as_int(session.get("ended_at_ms"))
    if ended is not None:
        return ended
    points = session.get("points")
    if isinstance(points, list) and points:
        last = points[-1]
        if isinstance(last, list) and last:
            stamp = _as_int(last[0])
            if stamp is not None:
                return stamp
    return _as_int(session.get("started_at_ms"))


def _sessions_can_merge(
    previous: dict[str, Any],
    continuation: dict[str, Any],
) -> bool:
    """Return whether two fragments belong to one logical mowing session."""
    if previous.get("legacy") or continuation.get("legacy"):
        return False
    # A vendor progress reset is an intentional mowing-cycle boundary, not a
    # short interruption. Never repair or resume across it even when the gap is
    # only a few seconds. An explicit reset can also be recorded on the first
    # session of the new cycle when there was no active previous session.
    if "reset" in str(previous.get("completion_reason") or ""):
        return False
    if _unique_ints(continuation.get("cycle_reset_zone_ids")):
        return False
    previous_end = _session_end_ms(previous)
    continuation_start = _as_int(continuation.get("started_at_ms"))
    if previous_end is None or continuation_start is None:
        return False
    gap_ms = continuation_start - previous_end
    return -_SESSION_CLOCK_SKEW_MS <= gap_ms <= _SESSION_MERGE_GAP_MS


def _merge_session_records(
    previous: dict[str, Any],
    continuation: dict[str, Any],
) -> dict[str, Any]:
    """Merge a later fragment into the earlier persistent session record."""
    merged = deepcopy(previous)
    merged["sn"] = previous.get("sn") or continuation.get("sn")
    merged["id"] = previous.get("id")
    merged["sequence"] = previous.get("sequence")
    continuation_active = bool(continuation.get("active"))
    continuation_end_ms = _session_end_ms(continuation)
    merged["active"] = continuation_active
    merged["ended_at_ms"] = None if continuation_active else continuation_end_ms
    merged["ended_at"] = (
        None
        if continuation_active
        else continuation.get("ended_at") or _iso(continuation_end_ms)
    )
    merged["approximate_timestamps"] = bool(
        previous.get("approximate_timestamps")
        or continuation.get("approximate_timestamps")
    )
    merged["mode"] = previous.get("mode") or continuation.get("mode") or "mowing"
    merged["zone_ids"] = _unique_ints(
        previous.get("zone_ids"), continuation.get("zone_ids")
    )
    merged["cutting_height_mm"] = (
        previous.get("cutting_height_mm")
        if previous.get("cutting_height_mm") is not None
        else continuation.get("cutting_height_mm")
    )
    merged["completed"] = (
        None
        if continuation_active
        else (
            continuation.get("completed")
            if continuation.get("completed") is not None
            else previous.get("completed")
        )
    )
    merged["completion_reason"] = (
        None
        if continuation_active
        else continuation.get("completion_reason") or previous.get("completion_reason")
    )
    final_progress = dict(previous.get("final_progress") or {})
    final_progress.update(continuation.get("final_progress") or {})
    merged["final_progress"] = final_progress
    merged["cycle_reset_zone_ids"] = _unique_ints(
        previous.get("cycle_reset_zone_ids"),
        continuation.get("cycle_reset_zone_ids"),
    )

    points = [
        list(point)
        for point in [
            *(previous.get("points") or []),
            *(continuation.get("points") or []),
        ]
        if isinstance(point, list)
    ]
    points.sort(key=lambda point: _as_int(point[0]) or 0 if point else 0)
    deduplicated: list[list[Any]] = []
    for point in points:
        if not deduplicated or point != deduplicated[-1]:
            deduplicated.append(point)
    merged["points"] = deduplicated

    segment_starts = [
        *(
            previous.get("segment_starts_ms")
            or [previous.get("started_at_ms")]
        ),
        *(
            continuation.get("segment_starts_ms")
            or [continuation.get("started_at_ms")]
        ),
    ]
    merged["segment_starts_ms"] = sorted(
        dict.fromkeys(
            stamp
            for stamp in (_as_int(value) for value in segment_starts)
            if stamp is not None
        )
    )
    return merged


class NavimowerHistory:
    """Own persistent trail sessions and zone-history timestamps for one mower."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        sn: str,
        *,
        retention_days: int = DEFAULT_TRAIL_RETENTION_DAYS,
        include_return_trail: bool = DEFAULT_INCLUDE_RETURN_TRAIL,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.sn = sn
        self.retention_days = max(0, int(retention_days))
        self.include_return_trail = bool(include_return_trail)
        self._index_store = _index_store(hass, entry_id)
        self._lock = threading.RLock()
        self._sequence = 0
        self._active_id: str | None = None
        self._sessions: list[dict[str, Any]] = []
        self._cache: dict[str, dict[str, Any]] = {}
        # Store.async_delay_save debounces per Store instance. Reuse one Store
        # per session so a 2-second MQTT stream creates one delayed checkpoint,
        # not hundreds of independent scheduled writes.
        self._session_stores: dict[str, Store] = {}
        self._zone_history: dict[str, dict[str, Any]] = {}
        self._trail_revision = 0
        # Last observed vendor progress per zone. This is persisted so a cycle
        # restart can still be recognized after a Home Assistant restart.
        self._zone_progress_state: dict[str, dict[str, Any]] = {}
        # A detected cycle restart must never be merged back into the previous
        # session by the normal five-minute interruption rule.
        self._force_new_session_once = False
        # Zone ids whose previous same-day trail must be replaced when the next
        # confirmed session first enters that zone. This is separate from the
        # session id because a battery-charge continuation may legitimately use
        # a new session without starting a new mowing cycle.
        self._force_new_cycle_zone_ids: list[int] = []
        self._last_cycle_event: dict[str, Any] | None = None

    # ---------------------------------------------------------------- load
    async def async_load(self) -> None:
        """Load the index, active session and a bounded recent-session cache."""
        try:
            data = await self._index_store.async_load()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("History index load failed", exc_info=True)
            data = None
        if isinstance(data, dict) and data.get("sn") in (None, self.sn):
            self._sequence = max(0, _as_int(data.get("sequence")) or 0)
            self._active_id = (
                str(data.get("active_id")) if data.get("active_id") else None
            )
            sessions = data.get("sessions")
            if isinstance(sessions, list):
                self._sessions = [
                    dict(item) for item in sessions if isinstance(item, dict)
                ]
            zone_history = data.get("zone_history")
            if isinstance(zone_history, dict):
                self._zone_history = {
                    str(key): dict(value)
                    for key, value in zone_history.items()
                    if isinstance(value, dict)
                }
            zone_progress_state = data.get("zone_progress_state")
            if isinstance(zone_progress_state, dict):
                self._zone_progress_state = {
                    str(key): dict(value)
                    for key, value in zone_progress_state.items()
                    if isinstance(value, dict)
                }
            self._force_new_session_once = bool(
                data.get("force_new_session_once")
            )
            force_new_cycle_zone_ids = data.get("force_new_cycle_zone_ids")
            if isinstance(force_new_cycle_zone_ids, list):
                self._force_new_cycle_zone_ids = _unique_ints(
                    force_new_cycle_zone_ids
                )
                self._force_new_session_once = bool(
                    self._force_new_session_once
                    or self._force_new_cycle_zone_ids
                )
            last_cycle_event = data.get("last_cycle_event")
            if isinstance(last_cycle_event, dict):
                self._last_cycle_event = dict(last_cycle_event)

        await self._async_prune(save=False)
        ids = [
            str(item.get("id"))
            for item in self._sessions[-SESSION_CACHE_LIMIT:]
            if item.get("id")
        ]
        if self._active_id and self._active_id not in ids:
            ids.append(self._active_id)
        for session_id in ids:
            session = await self._async_load_session_file(session_id)
            if session is not None:
                self._cache[session_id] = session
        if self._active_id not in self._cache:
            orphaned_active = self._active_id
            self._active_id = None
            if orphaned_active:
                self._sessions = [
                    item
                    for item in self._sessions
                    if str(item.get("id") or "") != orphaned_active
                ]
                _LOGGER.warning(
                    "Removed orphaned Navimower active-session index entry %s",
                    orphaned_active,
                )
                await self._index_store.async_save(self._index_data())

        await self._async_merge_adjacent_sessions()
        await self._async_remove_empty_completed_sessions()
        await self._async_migrate_legacy_store()
        with self._lock:
            self._trail_revision = max(
                self._sequence,
                sum(_as_int(item.get("point_count")) or 0 for item in self._sessions),
            )
        _LOGGER.debug(
            "Loaded %d Navimower session records (%d cached), active=%s",
            len(self._sessions),
            len(self._cache),
            self._active_id,
        )

    def _session_store_for(self, session_id: str) -> Store:
        store = self._session_stores.get(session_id)
        if store is None:
            store = _session_store(self.hass, self.entry_id, session_id)
            self._session_stores[session_id] = store
        return store

    async def _async_load_session_file(self, session_id: str) -> dict[str, Any] | None:
        try:
            session = await self._session_store_for(session_id).async_load()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Session %s load failed", session_id, exc_info=True)
            return None
        if not isinstance(session, dict) or session.get("sn") not in (None, self.sn):
            return None
        return session

    async def _async_merge_adjacent_sessions(self) -> None:
        """Repair persisted session fragments separated by at most five minutes."""
        changed = False
        index = 0
        while index < len(self._sessions) - 1:
            previous_meta = self._sessions[index]
            continuation_meta = self._sessions[index + 1]
            if not _sessions_can_merge(previous_meta, continuation_meta):
                index += 1
                continue

            previous_id = str(previous_meta.get("id") or "")
            continuation_id = str(continuation_meta.get("id") or "")
            if not previous_id or not continuation_id:
                index += 1
                continue

            previous = self._cache.get(previous_id)
            if previous is None:
                previous = await self._async_load_session_file(previous_id)
            continuation = self._cache.get(continuation_id)
            if continuation is None:
                continuation = await self._async_load_session_file(continuation_id)
            if previous is None or continuation is None:
                index += 1
                continue

            merged = _merge_session_records(previous, continuation)
            try:
                await self._session_store_for(previous_id).async_save(merged)
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "Could not persist merged Navimower session %s",
                    previous_id,
                    exc_info=True,
                )
                index += 1
                continue

            with self._lock:
                self._cache[previous_id] = merged
                self._cache.pop(continuation_id, None)
                self._sessions[index] = _metadata(merged)
                self._sessions.pop(index + 1)
                if self._active_id == continuation_id:
                    self._active_id = previous_id

            try:
                await self._session_store_for(continuation_id).async_remove()
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Could not remove merged session fragment %s",
                    continuation_id,
                    exc_info=True,
                )
            self._session_stores.pop(continuation_id, None)
            changed = True
            _LOGGER.info(
                "Merged Navimower session fragment %s into %s",
                continuation_id,
                previous_id,
            )
            # Keep the same index: the merged record may also join the next part.

        if changed:
            self._trim_cache_locked()
            try:
                await self._index_store.async_save(self._index_data())
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "Could not persist the merged Navimower session index",
                    exc_info=True,
                )

    async def _async_remove_empty_completed_sessions(self) -> None:
        """Delete persisted zero/one-point stubs created by older start/reset races."""
        removable = [
            str(item.get("id"))
            for item in self._sessions
            if item.get("id")
            and str(item.get("id")) != str(self._active_id or "")
            and (_as_int(item.get("point_count")) or 0) < 2
        ]
        if not removable:
            return
        with self._lock:
            remove_set = set(removable)
            self._sessions = [
                item for item in self._sessions
                if str(item.get("id") or "") not in remove_set
            ]
            for session_id in removable:
                self._cache.pop(session_id, None)
        for session_id in removable:
            try:
                await self._session_store_for(session_id).async_remove()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Could not remove empty session %s", session_id, exc_info=True)
            self._session_stores.pop(session_id, None)
        await self._index_store.async_save(self._index_data())
        _LOGGER.info("Removed %d empty Navimower session stub(s)", len(removable))

    async def _async_migrate_legacy_store(self) -> None:
        if self._sessions or self._active_id:
            return
        legacy = legacy_trail_store(self.hass, self.entry_id)
        try:
            data = await legacy.async_load()
        except Exception:  # noqa: BLE001
            return
        imported = await self.async_import_legacy_trail(data)
        if imported or data:
            try:
                await legacy.async_remove()
            except Exception:  # noqa: BLE001
                pass

    async def async_import_legacy_trail(self, data: Any) -> bool:
        """Convert one v0.1.x XY trail to a completed approximate session."""
        if self._sessions or self._active_id:
            return False
        if not isinstance(data, dict) or data.get("sn") not in (None, self.sn):
            return False
        raw = data.get("trail")
        if not isinstance(raw, list) or not raw:
            return False
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - max(0, len(raw) - 1) * 2000
        points: list[list[Any]] = []
        for index, point in enumerate(raw):
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            x, y = _as_float(point[0]), _as_float(point[1])
            if x is None or y is None:
                continue
            points.append(
                [start_ms + index * 2000, x, y, None, "legacy", None, None]
            )
        if not points:
            return False
        self._sequence += 1
        session_id = f"legacy-{now_ms}-{self._sequence}"
        session = {
            "sn": self.sn,
            "id": session_id,
            "sequence": self._sequence,
            "started_at_ms": points[0][0],
            "started_at": _iso(points[0][0]),
            "ended_at_ms": points[-1][0],
            "ended_at": _iso(points[-1][0]),
            "active": False,
            "legacy": True,
            "approximate_timestamps": True,
            "mode": "legacy_import",
            "zone_ids": [],
            "cutting_height_mm": None,
            "completed": None,
            "completion_reason": "legacy_import",
            "final_progress": {},
            "segment_starts_ms": [points[0][0]],
            "points": points,
        }
        self._cache[session_id] = session
        self._sessions.append(_metadata(session))
        await self._session_store_for(session_id).async_save(session)
        await self._index_store.async_save(self._index_data())
        return True

    # ------------------------------------------------------------- snapshots
    def _index_data(self) -> dict[str, Any]:
        with self._lock:
            return {
                "sn": self.sn,
                "sequence": self._sequence,
                "active_id": self._active_id,
                "sessions": deepcopy(self._sessions),
                "zone_history": deepcopy(self._zone_history),
                "zone_progress_state": deepcopy(self._zone_progress_state),
                "force_new_session_once": self._force_new_session_once,
                "force_new_cycle_zone_ids": list(self._force_new_cycle_zone_ids),
                "last_cycle_event": deepcopy(self._last_cycle_event),
            }

    def _active_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            session = self._cache.get(self._active_id or "")
            return deepcopy(session) if session else None

    def _schedule_index_save(self) -> None:
        self._index_store.async_delay_save(
            self._index_data, MQTT_HISTORY_SAVE_DELAY_SECONDS
        )

    def _schedule_active_save(self) -> None:
        active_id = self._active_id
        if active_id is None:
            return
        store = self._session_store_for(active_id)

        def _snapshot_for_session() -> dict[str, Any] | None:
            # Capture the session id now. If the session finishes before the
            # delayed save executes, its final immutable snapshot is still
            # written instead of ``None``.
            with self._lock:
                session = self._cache.get(active_id)
                return deepcopy(session) if session else None

        store.async_delay_save(_snapshot_for_session, MQTT_HISTORY_SAVE_DELAY_SECONDS)

    # --------------------------------------------------------------- observe
    def process_pose(
        self,
        *,
        position: dict[str, Any] | None,
        pose_time: Any,
        heading: Any,
        activity: str,
        cutting: bool,
        docked: bool,
        returning: bool,
        zone_ids: list[int] | None = None,
        cutting_height_mm: int | None = None,
        mode: str | None = None,
        mqtt_vehicle_state: int | None = None,
        mqtt_action: int | None = None,
        physical_zone_id: int | None = None,
        completed: bool | None = None,
    ) -> None:
        """Observe one state/pose update and maintain the active session.

        A session starts on the first cutting state, survives pause/transit and
        (by default) return-to-dock, and closes only on a non-cutting docked
        state. Only an exactly repeated full pose/context sample is discarded.
        """
        with self._lock:
            observed_zone_ids = _unique_ints(
                zone_ids or [],
                [physical_zone_id] if physical_zone_id is not None else [],
            )
            if cutting and self._active_id is None:
                self._start_session_locked(
                    pose_time=pose_time,
                    zone_ids=observed_zone_ids,
                    cutting_height_mm=cutting_height_mm,
                    mode=mode,
                )

            active = self._cache.get(self._active_id or "")
            if active is None:
                return

            if observed_zone_ids:
                active["zone_ids"] = list(
                    dict.fromkeys(
                        [*(active.get("zone_ids") or []), *observed_zone_ids]
                    )
                )
                task_progress = active.setdefault("task_zone_progress", {})
                for zone_id in active["zone_ids"]:
                    task_progress.setdefault(str(zone_id), 0)
            if physical_zone_id is not None:
                visited = active.setdefault("visited_zone_ids", [])
                first_visit = physical_zone_id not in visited
                if first_visit:
                    visited.append(physical_zone_id)
                if cutting:
                    observed_ms = _timestamp_ms(pose_time)
                    observed_iso = _iso(observed_ms)
                    record = dict(
                        self._zone_history.get(str(physical_zone_id)) or {}
                    )
                    record.update(
                        {
                            "id": physical_zone_id,
                            "name": record.get("name")
                            or f"Zone {physical_zone_id}",
                            "cycle_id": active.get("id"),
                            "last_mowed_at": observed_iso,
                        }
                    )
                    if first_visit:
                        record["last_started_at"] = observed_iso
                    self._zone_history[str(physical_zone_id)] = record
            if active.get("cutting_height_mm") is None and cutting_height_mm is not None:
                active["cutting_height_mm"] = cutting_height_mm
            if mode and not active.get("mode"):
                active["mode"] = mode
            if completed is not None:
                active["completed"] = completed

            # Once a mowing session exists, preserve transit and pause positions.
            # The return path is optional because some users only want cut lines.
            should_store_pose = (
                (not returning and not docked) or self.include_return_trail
            )
            if position is not None and should_store_pose:
                before_count = len(active.get("points") or [])
                self._append_point_locked(
                    active,
                    position=position,
                    pose_time=pose_time,
                    heading=heading,
                    activity=activity,
                    mqtt_vehicle_state=mqtt_vehicle_state,
                    mqtt_action=mqtt_action,
                    physical_zone_id=physical_zone_id,
                )
                if len(active.get("points") or []) > before_count:
                    self._trail_revision += 1

            if docked and not cutting:
                if active.get("completed") is True:
                    completed_ms = _timestamp_ms(pose_time)
                    final_progress = dict(active.get("final_progress") or {})
                    for zone_id in _unique_ints(active.get("zone_ids")):
                        state = self._zone_progress_state.get(str(zone_id)) or {}
                        progress = _as_int(state.get("progress"))
                        if progress is not None:
                            final_progress[str(zone_id)] = progress
                        record = dict(self._zone_history.get(str(zone_id)) or {})
                        record.update(
                            {
                                "id": zone_id,
                                "name": record.get("name") or f"Zone {zone_id}",
                                "last_completed_at": _iso(completed_ms),
                                "last_completed_progress": progress,
                            }
                        )
                        self._zone_history[str(zone_id)] = record
                    active["final_progress"] = final_progress
                    active["completion_reason"] = (
                        active.get("completion_reason") or "dock_completed"
                    )
                    self._schedule_index_save()
                self._finish_active_locked(pose_time)
            else:
                self._update_active_metadata_locked(active)
                self._schedule_active_save()
                self._schedule_index_save()

    # Backward-compatible alias used by older internal experiments.
    observe = process_pose

    def _start_session_locked(
        self,
        *,
        pose_time: Any,
        zone_ids: list[int],
        cutting_height_mm: int | None,
        mode: str | None,
    ) -> None:
        start_ms = _timestamp_ms(pose_time)
        force_new = self._force_new_session_once
        cycle_reset_zone_ids = (
            list(self._force_new_cycle_zone_ids) if force_new else []
        )
        self._force_new_session_once = False
        self._force_new_cycle_zone_ids = []
        if not force_new and self._resume_recent_session_locked(
            start_ms=start_ms,
            zone_ids=zone_ids,
            cutting_height_mm=cutting_height_mm,
            mode=mode,
        ):
            return
        self._sequence += 1
        session_id = f"{start_ms}-{self._sequence}"
        session = {
            "sn": self.sn,
            "id": session_id,
            "sequence": self._sequence,
            "started_at_ms": start_ms,
            "started_at": _iso(start_ms),
            "ended_at_ms": None,
            "ended_at": None,
            "active": True,
            "legacy": False,
            "approximate_timestamps": False,
            "mode": mode or "mowing",
            "zone_ids": list(dict.fromkeys(int(value) for value in zone_ids)),
            "cutting_height_mm": cutting_height_mm,
            "completed": None,
            "completion_reason": None,
            "final_progress": {},
            "cycle_reset_zone_ids": cycle_reset_zone_ids,
            "visited_zone_ids": [],
            "task_zone_progress": {str(value): 0 for value in zone_ids},
            "segment_starts_ms": [start_ms],
            "points": [],
        }
        self._active_id = session_id
        self._cache[session_id] = session
        self._sessions.append(_metadata(session))
        self._trim_cache_locked()
        self._trail_revision += 1
        self._schedule_index_save()
        _LOGGER.debug("Started Navimower session %s", session_id)

    def _resume_recent_session_locked(
        self,
        *,
        start_ms: int,
        zone_ids: list[int],
        cutting_height_mm: int | None,
        mode: str | None,
    ) -> bool:
        """Reopen the latest session after a short stop/reload/restart gap."""
        if not self._sessions:
            return False
        previous_meta = self._sessions[-1]
        candidate = {
            "started_at_ms": start_ms,
            "legacy": False,
        }
        if not _sessions_can_merge(previous_meta, candidate):
            return False
        session_id = str(previous_meta.get("id") or "")
        previous = self._cache.get(session_id)
        if not session_id or previous is None or previous.get("active"):
            return False

        previous["active"] = True
        previous["ended_at_ms"] = None
        previous["ended_at"] = None
        previous["completed"] = None
        previous["completion_reason"] = None
        previous["final_progress"] = {}
        previous.setdefault("visited_zone_ids", [])
        previous.setdefault("task_zone_progress", {})
        previous["zone_ids"] = _unique_ints(
            previous.get("zone_ids"), zone_ids
        )
        for zone_id in previous["zone_ids"]:
            previous["task_zone_progress"].setdefault(str(zone_id), 0)
        if previous.get("cutting_height_mm") is None:
            previous["cutting_height_mm"] = cutting_height_mm
        if not previous.get("mode"):
            previous["mode"] = mode or "mowing"
        segment_starts = previous.setdefault(
            "segment_starts_ms",
            [previous.get("started_at_ms")],
        )
        if start_ms not in segment_starts:
            segment_starts.append(start_ms)

        self._active_id = session_id
        self._update_active_metadata_locked(previous)
        self._schedule_active_save()
        self._schedule_index_save()
        _LOGGER.info(
            "Resumed Navimower session %s after a short interruption",
            session_id,
        )
        return True

    @staticmethod
    def _append_point_locked(
        session: dict[str, Any],
        *,
        position: dict[str, Any],
        pose_time: Any,
        heading: Any,
        activity: str,
        mqtt_vehicle_state: int | None,
        mqtt_action: int | None,
        physical_zone_id: int | None,
    ) -> None:
        x = _as_float(position.get("x"))
        y = _as_float(position.get("y"))
        if x is None or y is None:
            return
        ts = _timestamp_ms(pose_time)
        hdg = _as_float(heading if heading is not None else position.get("heading"))
        sample = [
            ts,
            x,
            y,
            hdg,
            str(activity or "unknown"),
            mqtt_vehicle_state,
            mqtt_action,
            physical_zone_id,
        ]
        points = session.setdefault("points", [])
        if points:
            previous = points[-1]
            # Suppress only a byte-for-byte duplicate delivery. A later sample
            # with the same pose is still retained because its timestamp carries
            # useful dwell/history information.
            if isinstance(previous, list) and previous == sample:
                return
        points.append(sample)
        # Instance-level revision lets the map API cache daily trails while still
        # changing on every genuinely retained live point.
        # This is a static method, so the caller increments the owner revision.

    def _update_active_metadata_locked(self, active: dict[str, Any]) -> None:
        meta = _metadata(active)
        for index, existing in enumerate(self._sessions):
            if existing.get("id") == active.get("id"):
                self._sessions[index] = meta
                return

    @staticmethod
    def _is_provisional_session(session: dict[str, Any] | None) -> bool:
        """Return whether a just-created session has no drawable route yet."""
        if not isinstance(session, dict):
            return False
        points = session.get("points") or []
        return len(points) < 2

    def _discard_active_locked(self) -> None:
        """Remove an empty start/reset stub instead of publishing it as history."""
        session_id = self._active_id
        if session_id is None:
            return
        self._active_id = None
        self._cache.pop(session_id, None)
        self._sessions = [row for row in self._sessions if row.get("id") != session_id]
        store = self._session_stores.pop(session_id, None)
        if store is not None:
            self.hass.async_create_task(
                store.async_remove(),
                f"Remove provisional Navimower session {session_id}",
            )
        self._trail_revision += 1
        self._schedule_index_save()
        _LOGGER.debug("Discarded provisional Navimower session %s", session_id)

    def _finish_active_locked(self, pose_time: Any) -> None:
        session_id = self._active_id
        if session_id is None:
            return
        active = self._cache.get(session_id)
        if active is None:
            self._active_id = None
            return
        end_ms = _timestamp_ms(pose_time)
        if active.get("points"):
            end_ms = max(end_ms, _as_int(active["points"][-1][0]) or end_ms)
        active.update(
            {
                "ended_at_ms": end_ms,
                "ended_at": _iso(end_ms),
                "active": False,
            }
        )
        self._update_active_metadata_locked(active)
        self._active_id = None
        self._trail_revision += 1
        self.hass.async_create_task(
            self._async_finalize_session(session_id, deepcopy(active)),
            f"Finalize Navimower session {session_id}",
        )
        _LOGGER.debug(
            "Finished Navimower session %s with %d points",
            session_id,
            len(active.get("points") or []),
        )

    async def _async_finalize_session(
        self, session_id: str, session: dict[str, Any]
    ) -> None:
        try:
            # A new cutting update may have reopened this session before the
            # asynchronous finalizer runs. Always persist the newest cache
            # snapshot so a stale completed copy cannot overwrite the resume.
            with self._lock:
                current = deepcopy(self._cache.get(session_id))
            snapshot = current or session
            store = self._session_store_for(session_id)
            await store.async_save(snapshot)

            # The session can resume while Store.async_save is awaiting I/O. If
            # that happened, immediately replace the stale finalized snapshot
            # with the newest active state instead of waiting for the delayed
            # checkpoint.
            with self._lock:
                latest = deepcopy(self._cache.get(session_id))
            if latest is not None and latest != snapshot:
                await store.async_save(latest)

            await self._async_prune(save=False)
            await self._index_store.async_save(self._index_data())
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Could not finalize session %s", session_id, exc_info=True)

    # ----------------------------------------------------------- cycle tracking
    @staticmethod
    def _progress_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """Return one normalized progress row per mapped zone."""
        coverage_by_id = {
            _as_int(item.get("id")): item
            for item in (snapshot.get("coverage") or {}).get("zones") or []
            if isinstance(item, dict) and _as_int(item.get("id")) is not None
        }
        details_by_id = {
            _as_int(item.get("id")): item
            for item in snapshot.get("zone_details") or []
            if isinstance(item, dict) and _as_int(item.get("id")) is not None
        }
        rows: list[dict[str, Any]] = []
        for zone_id in sorted(set(coverage_by_id) | set(details_by_id)):
            if zone_id is None:
                continue
            coverage = coverage_by_id.get(zone_id) or {}
            detail = details_by_id.get(zone_id) or {}
            progress = _as_int(
                detail.get("progress")
                if detail.get("progress") is not None
                else detail.get("percentage")
                if detail.get("percentage") is not None
                else coverage.get("pct")
            )
            rows.append(
                {
                    "id": zone_id,
                    "name": detail.get("name") or coverage.get("name") or f"Zone {zone_id}",
                    "progress": progress,
                    "start_time": _as_int(coverage.get("start_time")),
                    "end_time": _as_int(coverage.get("end_time")),
                }
            )
        return rows

    def start_new_cycle(
        self,
        *,
        pose_time: Any,
        zone_ids: list[int] | None = None,
        reason: str = "ha_reset_command",
    ) -> bool:
        """Create an explicit cycle boundary after a successful reset command."""
        boundary_ms = _timestamp_ms(pose_time)
        requested = _unique_ints(zone_ids or [])
        with self._lock:
            active = self._cache.get(self._active_id or "")
            active_zones = _unique_ints((active or {}).get("zone_ids") or [])
            relevant = active_zones or requested
            final_progress: dict[str, int] = {}
            known_progress: list[int] = []
            for zone_id in relevant:
                state = self._zone_progress_state.get(str(zone_id)) or {}
                progress = _as_int(state.get("peak_progress"))
                if progress is None:
                    progress = _as_int(state.get("progress"))
                if progress is not None:
                    final_progress[str(zone_id)] = progress
                    known_progress.append(progress)
            completed = bool(known_progress) and all(
                value >= VENDOR_COMPLETION_PROGRESS_MIN for value in known_progress
            )
            if active is not None:
                completed = bool(active.get("completed") is True or completed)
                active["completed"] = completed
                active["completion_reason"] = reason
                active["final_progress"] = final_progress
                self._update_active_metadata_locked(active)
                if self._is_provisional_session(active):
                    self._discard_active_locked()
                else:
                    self._finish_active_locked(boundary_ms)
            if completed:
                for zone_id in relevant:
                    progress = final_progress.get(str(zone_id))
                    record = dict(self._zone_history.get(str(zone_id)) or {})
                    record.update({
                        "id": zone_id,
                        "name": record.get("name") or f"Zone {zone_id}",
                        "last_completed_at": _iso(boundary_ms),
                        "last_completed_progress": progress,
                    })
                    self._zone_history[str(zone_id)] = record
            self._force_new_session_once = True
            self._force_new_cycle_zone_ids = list(relevant)
            self._last_cycle_event = {
                "reason": reason,
                "at_ms": boundary_ms,
                "at": _iso(boundary_ms),
                "zone_ids": relevant,
                "completed": completed,
                "final_progress": final_progress,
                "source": "explicit_command",
            }
        self._schedule_index_save()
        _LOGGER.info(
            "Started a new Navimower mowing cycle from %s for zone(s) %s",
            reason, ", ".join(str(value) for value in relevant) or "all",
        )
        return True

    def prepare_cycle(
        self,
        snapshot: dict[str, Any],
        *,
        pose_time: Any,
    ) -> bool:
        """Split the active route when vendor progress starts a new cycle."""
        rows = self._progress_rows(snapshot)
        if not rows:
            return False
        reset_rows: list[tuple[dict[str, Any], dict[str, Any], int]] = []
        with self._lock:
            active = self._cache.get(self._active_id or "")
            for row in rows:
                zone_id = _as_int(row.get("id"))
                if zone_id is None:
                    continue
                previous = dict(self._zone_progress_state.get(str(zone_id)) or {})
                old_progress = _as_int(previous.get("progress"))
                peak_progress = _as_int(previous.get("peak_progress"))
                if peak_progress is None:
                    peak_progress = old_progress
                new_progress = _as_int(row.get("progress"))
                old_start = _as_int(previous.get("start_time"))
                new_start = _as_int(row.get("start_time"))
                drop = (
                    peak_progress - new_progress
                    if peak_progress is not None and new_progress is not None
                    else None
                )
                hard_reset = bool(
                    drop is not None and (
                        (peak_progress >= 15 and new_progress <= 5 and drop >= 15)
                        or (peak_progress >= 50 and new_progress <= 20 and drop >= 30)
                    )
                )
                timestamp_reset = bool(
                    old_progress is not None and new_progress is not None
                    and old_start is not None and new_start is not None
                    and new_start > old_start and new_progress <= 25
                    and old_progress - new_progress >= 10
                )
                if active is not None and (hard_reset or timestamp_reset):
                    reset_rows.append((previous, row, peak_progress or old_progress or 0))

            if reset_rows and active is not None:
                final_progress: dict[str, int] = {}
                completion_ms = _timestamp_ms(pose_time)
                completion_flags: list[bool] = []
                for _previous, row, previous_peak in reset_rows:
                    zone_id = _as_int(row.get("id"))
                    if zone_id is None:
                        continue
                    final_progress[str(zone_id)] = previous_peak
                    completed_zone = previous_peak >= VENDOR_COMPLETION_PROGRESS_MIN
                    completion_flags.append(completed_zone)
                    new_start = _as_int(row.get("start_time"))
                    if new_start is not None:
                        completion_ms = min(completion_ms, _timestamp_ms(new_start))
                    if completed_zone:
                        record = dict(self._zone_history.get(str(zone_id)) or {})
                        record.update({
                            "id": zone_id,
                            "name": row.get("name") or record.get("name") or f"Zone {zone_id}",
                            "last_completed_at": _iso(completion_ms),
                            "last_completed_progress": previous_peak,
                        })
                        self._zone_history[str(zone_id)] = record
                completed_cycle = bool(completion_flags) and all(completion_flags)
                active["completed"] = completed_cycle
                active["completion_reason"] = (
                    "vendor_cycle_reset"
                    if completed_cycle
                    else "vendor_cycle_reset_partial"
                )
                active["final_progress"] = final_progress
                self._update_active_metadata_locked(active)
                reset_zone_ids = [
                    _as_int(row.get("id"))
                    for _previous, row, _peak in reset_rows
                    if _as_int(row.get("id")) is not None
                ]
                self._force_new_session_once = True
                self._force_new_cycle_zone_ids = _unique_ints(reset_zone_ids)
                if self._is_provisional_session(active):
                    self._discard_active_locked()
                else:
                    self._finish_active_locked(pose_time)
                self._last_cycle_event = {
                    "reason": active["completion_reason"],
                    "at_ms": completion_ms,
                    "at": _iso(completion_ms),
                    "zone_ids": reset_zone_ids,
                    "completed": completed_cycle,
                    "final_progress": final_progress,
                    "source": "vendor_progress",
                }
                _LOGGER.info(
                    "Started a new Navimower mowing cycle after progress reset in zone(s) %s",
                    ", ".join(str(row.get("id")) for _previous, row, _peak in reset_rows),
                )

            reset_zone_ids = {_as_int(row.get("id")) for _previous, row, _peak in reset_rows}
            for row in rows:
                zone_id = _as_int(row.get("id"))
                if zone_id is None:
                    continue
                progress = _as_int(row.get("progress"))
                previous = self._zone_progress_state.get(str(zone_id)) or {}
                previous_peak = _as_int(previous.get("peak_progress"))
                if previous_peak is None:
                    previous_peak = _as_int(previous.get("progress"))
                if zone_id in reset_zone_ids:
                    peak = progress
                else:
                    known = [value for value in (previous_peak, progress) if value is not None]
                    peak = max(known) if known else None
                self._zone_progress_state[str(zone_id)] = {
                    "progress": progress,
                    "peak_progress": peak,
                    "start_time": _as_int(row.get("start_time")),
                    "end_time": _as_int(row.get("end_time")),
                    "observed_at_ms": _timestamp_ms(pose_time),
                }
        self._schedule_index_save()
        return bool(reset_rows)

    def cycle_diagnostics(self) -> dict[str, Any]:
        """Return non-sensitive cycle state for diagnostics."""
        with self._lock:
            return {
                "last_event": deepcopy(self._last_cycle_event),
                "zone_progress_state": deepcopy(self._zone_progress_state),
                "force_new_session_once": self._force_new_session_once,
                "force_new_cycle_zone_ids": list(self._force_new_cycle_zone_ids),
            }

    # ----------------------------------------------------------- zone history
    def update_zone_history(
        self,
        coverage: dict[str, Any] | None,
        zone_details: list[dict[str, Any]],
        *,
        active_zone_progress: Any = None,
        cycle_reset_pending: bool = False,
    ) -> None:
        """Persist the latest known timestamps/progress for every zone."""
        coverage_by_id = {
            _as_int(item.get("id")): item
            for item in (coverage or {}).get("zones") or []
            if isinstance(item, dict) and _as_int(item.get("id")) is not None
        }
        changed = False
        with self._lock:
            for detail in zone_details:
                if not isinstance(detail, dict):
                    continue
                zone_id = _as_int(detail.get("id"))
                if zone_id is None:
                    continue
                row = coverage_by_id.get(zone_id) or {}
                record = dict(self._zone_history.get(str(zone_id)) or {})
                record.update(
                    {
                        "id": zone_id,
                        "name": detail.get("name") or f"Zone {zone_id}",
                        "area_m2": detail.get("area_m2", row.get("area")),
                        "finished_area_m2": detail.get(
                            "finished_area_m2", row.get("finished")
                        ),
                        "percentage": (
                            detail.get("progress")
                            if detail.get("progress") is not None
                            else detail.get("percentage", row.get("pct"))
                        ),
                        "vendor_percentage": detail.get("vendor_percentage", row.get("pct")),
                        "progress_source": detail.get("progress_source") or "coverage",
                        "cutting_height_mm": detail.get("cutting_height_mm"),
                        "inherits_global_height": detail.get(
                            "inherits_global_height"
                        ),
                    }
                )
                for key in (
                    "last_started_at",
                    "last_mowed_at",
                    "last_completed_at",
                ):
                    value = detail.get(key)
                    if value:
                        # ISO timestamps are lexicographically ordered when all
                        # values are normalized UTC ISO strings.
                        previous = record.get(key)
                        record[key] = max(str(previous or ""), str(value))
                active = self._cache.get(self._active_id or "")
                if active is not None and zone_id in set(active.get("visited_zone_ids") or []):
                    record["cycle_id"] = active.get("id")
                self._zone_history[str(zone_id)] = record
                changed = True

            active = self._cache.get(self._active_id or "")
            if active is not None:
                task_progress = active.setdefault("task_zone_progress", {})
                for zone_id in active.get("zone_ids") or []:
                    task_progress.setdefault(str(zone_id), 0)
                active_zone_id = None
                if active.get("visited_zone_ids"):
                    active_zone_id = _as_int(active.get("visited_zone_ids")[-1])
                if active_zone_id is not None:
                    active_detail = next(
                        (
                            item
                            for item in zone_details
                            if _as_int(item.get("id")) == active_zone_id
                        ),
                        None,
                    )
                    if active_detail is not None:
                        # Use only the coordinator's active-zone/work counter
                        # here. The separate overall task percentage must never be
                        # assigned to a zone. During a confirmed new-cycle hold the
                        # zone value remains low instead of reviving stale coverage.
                        progress = _as_int(active_zone_progress)
                        if progress is None and not cycle_reset_pending:
                            progress = _as_int(
                                active_detail.get("progress")
                                if active_detail.get("progress") is not None
                                else active_detail.get("percentage")
                            )
                        if progress is not None:
                            previous_progress = _as_int(
                                task_progress.get(str(active_zone_id))
                            )
                            vendor_progress = _as_int(
                                active_detail.get("vendor_percentage")
                                if active_detail.get("vendor_percentage") is not None
                                else active_detail.get("percentage")
                            )
                            stale_completed_value = bool(
                                previous_progress is not None
                                and previous_progress >= VENDOR_COMPLETION_PROGRESS_MIN
                                and progress < VENDOR_COMPLETION_PROGRESS_MIN
                                and vendor_progress is not None
                                and vendor_progress < VENDOR_COMPLETION_PROGRESS_MIN
                            )
                            if stale_completed_value:
                                # A restored/transition spike of 100% must not pin
                                # an actively mowed zone when both the fresh work
                                # counter and vendor coverage confirm it is still
                                # incomplete. This heals beta2-era session state.
                                task_progress[str(active_zone_id)] = progress
                                if active.get("completion_reason") == "vendor_progress":
                                    active["completed"] = None
                                    active["completion_reason"] = None
                                    active["final_progress"] = {}
                            else:
                                task_progress[str(active_zone_id)] = max(
                                    previous_progress or 0, progress
                                )
                if active.get("cutting_height_mm") is None:
                    target_ids = set(active.get("zone_ids") or [])
                    candidates = [
                        _as_int(item.get("cutting_height_mm"))
                        for item in zone_details
                        if not target_ids or _as_int(item.get("id")) in target_ids
                    ]
                    known = [value for value in candidates if value is not None]
                    if len(set(known)) == 1:
                        active["cutting_height_mm"] = known[0]
                percentages = [
                    _as_int(task_progress.get(str(zone_id)))
                    for zone_id in active.get("zone_ids") or []
                    if _as_int(task_progress.get(str(zone_id))) is not None
                ]
                if (
                    percentages
                    and len(percentages) == len(active.get("zone_ids") or [])
                    and all(value >= VENDOR_COMPLETION_PROGRESS_MIN for value in percentages)
                ):
                    active["completed"] = True
                    active["completion_reason"] = (
                        active.get("completion_reason") or "vendor_progress"
                    )
                elif active.get("completion_reason") == "vendor_progress":
                    # Recompute optimistic completion after a stale 100% value
                    # has been corrected by fresh active-zone telemetry.
                    active["completed"] = None
                    active["completion_reason"] = None
                    active["final_progress"] = {}
                self._update_active_metadata_locked(active)
                changed = True

        if changed:
            self._schedule_index_save()
            self._schedule_active_save()

    def update_from_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.update_zone_history(
            snapshot.get("coverage"),
            snapshot.get("zone_details") or [],
            active_zone_progress=snapshot.get("active_zone_progress"),
            cycle_reset_pending=bool(snapshot.get("cycle_value_reset_pending")),
        )

    # --------------------------------------------------------------- payload
    @property
    def active_session(self) -> dict[str, Any] | None:
        return self._active_snapshot()

    @property
    def trail_revision(self) -> int:
        with self._lock:
            return self._trail_revision

    @property
    def active_session_no(self) -> int:
        """Return a monotonic card revision for session start and finish.

        Encoding the active state into the sequence makes the map-data entity
        change once when a session starts and again when that same session is
        finalized. This guarantees that the standalone card refetches the
        backend session list instead of leaving the completed route only in
        browser memory.
        """
        with self._lock:
            if self._sequence <= 0:
                return 0
            return self._sequence * 2 + (0 if self._active_id else 1)

    @property
    def active_started_at_value(self) -> str | None:
        with self._lock:
            active = self._cache.get(self._active_id or "")
            return str(active.get("started_at")) if active and active.get("started_at") else None

    def active_started_at(self) -> str | None:
        return self.active_started_at_value

    def active_trail_xy(self) -> list[list[float]]:
        with self._lock:
            active = self._cache.get(self._active_id or "")
            return _card_points(active) if active else []

    def active_trail_segments_xy(self) -> list[list[list[float]]]:
        """Return active route fragments without drawing across interruptions."""
        with self._lock:
            active = self._cache.get(self._active_id or "")
            return _card_segments(active) if active else []

    def latest_trail_xy(self) -> list[list[float]]:
        """Return the active trail, or the most recent cached completed trail."""
        with self._lock:
            active = self._cache.get(self._active_id or "")
            if active is not None:
                return _card_points(active)
            for metadata in reversed(self._sessions):
                session_id = str(metadata.get("id") or "")
                session = self._cache.get(session_id)
                if session is not None and session.get("points"):
                    return _card_points(session)
        return []

    # Older internal name retained for compatibility.
    active_points_xy = active_trail_xy

    def zone_history(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return deepcopy(self._zone_history)

    async def async_full_sessions(self) -> list[dict[str, Any]]:
        """Load full retained sessions for integration-side route processing."""
        with self._lock:
            metadata = deepcopy(self._sessions)
            cache = deepcopy(self._cache)
        result: list[dict[str, Any]] = []
        for meta in metadata:
            session_id = str(meta.get("id") or "")
            full = cache.get(session_id)
            if full is None and session_id:
                full = await self._async_load_session_file(session_id)
            if isinstance(full, dict):
                result.append(full)
        return result

    async def async_daily_zone_trails(
        self, map_zones: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Return today's latest cycle trail per zone, ready for the map card."""
        sessions = await self.async_full_sessions()
        today = dt_util.now().date()

        def _local_date(timestamp_ms: int):
            value = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=UTC)
            return dt_util.as_local(value).date()

        return build_daily_trails(
            sessions=sessions,
            map_zones=map_zones,
            local_date=today,
            to_local_date=_local_date,
            revision=self.trail_revision,
        )

    def session_summaries(self, *, include_points: bool = False) -> list[dict[str, Any]]:
        """Return retained session metadata, and cached points when requested."""
        with self._lock:
            metadata = deepcopy(self._sessions)
            cache = deepcopy(self._cache)
        result: list[dict[str, Any]] = []
        for meta in metadata:
            session_id = str(meta.get("id") or "")
            full = cache.get(session_id)
            result.append(
                _card_session(full or meta, include_points=include_points and full is not None)
            )
        return result

    async def async_card_sessions(
        self, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Load retained sessions with full XY points for the map endpoint.

        ``limit`` remains available for internal callers, but the public map
        endpoint currently includes every session retained by the configured
        history policy. Dedicated index/detail endpoints expose lightweight
        metadata and exact timestamped samples for on-demand card loading.
        """
        with self._lock:
            metadata = deepcopy(self._sessions)
        if limit is not None and limit > 0:
            metadata = metadata[-limit:]
        result: list[dict[str, Any]] = []
        for meta in metadata:
            session_id = str(meta.get("id") or "")
            if not session_id:
                continue
            with self._lock:
                full = deepcopy(self._cache.get(session_id))
            if full is None:
                full = await self._async_load_session_file(session_id)
                if full is not None:
                    with self._lock:
                        self._cache[session_id] = full
                        self._trim_cache_locked()
            result.append(_card_session(full or meta, include_points=full is not None))
        return result

    def sessions_index_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "entry_id": self.entry_id,
                "retention_days": self.retention_days,
                "active_session_id": self._active_id,
                "session_detail_point_format": list(SESSION_DETAIL_POINT_FORMAT),
                "session_xy_point_format": list(SESSION_CARD_POINT_FORMAT),
                "sessions": deepcopy(list(reversed(self._sessions))),
            }

    async def async_session_payload(self, session_id: str) -> dict[str, Any] | None:
        """Return one retained session only when it is present in the index."""
        requested = str(session_id)
        with self._lock:
            known_ids = {
                str(item.get("id"))
                for item in self._sessions
                if isinstance(item, dict) and item.get("id")
            }
            if requested not in known_ids:
                return None
            session = deepcopy(self._cache.get(requested))
        if session is None:
            session = await self._async_load_session_file(requested)
        if session is None:
            return None
        with self._lock:
            self._cache[requested] = session
            self._trim_cache_locked()
        return deepcopy(session)

    # ------------------------------------------------------------- retention
    async def _async_prune(self, *, save: bool = True) -> None:
        if self.retention_days <= 0:
            return
        cutoff_ms = int(
            (datetime.now(tz=UTC) - timedelta(days=self.retention_days)).timestamp()
            * 1000
        )
        remove_ids: list[str] = []
        with self._lock:
            keep: list[dict[str, Any]] = []
            for meta in self._sessions:
                session_id = str(meta.get("id") or "")
                stamp = (
                    _as_int(meta.get("ended_at_ms"))
                    or _as_int(meta.get("started_at_ms"))
                    or 0
                )
                if session_id != self._active_id and stamp and stamp < cutoff_ms:
                    remove_ids.append(session_id)
                else:
                    keep.append(meta)
            self._sessions = keep
            for session_id in remove_ids:
                self._cache.pop(session_id, None)
        for session_id in remove_ids:
            try:
                await self._session_store_for(session_id).async_remove()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Session prune failed for %s", session_id, exc_info=True)
            finally:
                self._session_stores.pop(session_id, None)
        if save and remove_ids:
            await self._index_store.async_save(self._index_data())

    def _trim_cache_locked(self) -> None:
        allowed = {
            str(item.get("id"))
            for item in self._sessions[-SESSION_CACHE_LIMIT:]
            if item.get("id")
        }
        if self._active_id:
            allowed.add(self._active_id)
        for session_id in list(self._cache):
            if session_id not in allowed:
                self._cache.pop(session_id, None)

    async def async_checkpoint(self, *, force: bool = False) -> None:
        """Persist active history/index and apply retention."""
        del force  # Kept for the coordinator API; this call always flushes.
        active = self._active_snapshot()
        active_id = self._active_id
        if active and active_id:
            await self._session_store_for(active_id).async_save(active)
        await self._async_prune(save=False)
        await self._index_store.async_save(self._index_data())

    async_flush = async_checkpoint

    async def async_remove(self) -> None:
        await self.async_remove_all(self.hass, self.entry_id)

    @classmethod
    async def async_remove_all(cls, hass: HomeAssistant, entry_id: str) -> None:
        """Remove every session Store after a config entry is deleted."""
        index = _index_store(hass, entry_id)
        try:
            data = await index.async_load()
        except Exception:  # noqa: BLE001
            data = None
        ids = [
            str(item.get("id"))
            for item in (data.get("sessions") if isinstance(data, dict) else []) or []
            if isinstance(item, dict) and item.get("id")
        ]
        for session_id in ids:
            try:
                await _session_store(hass, entry_id, session_id).async_remove()
            except Exception:  # noqa: BLE001
                pass
        for store in (index, legacy_trail_store(hass, entry_id)):
            try:
                await store.async_remove()
            except Exception:  # noqa: BLE001
                pass
