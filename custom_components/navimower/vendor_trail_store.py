"""Persistent per-zone vendor geometry, keyed exclusively by ZoneLedger cycles.

The ledger and its current-cycle artifacts share one atomic HA storage record.
Polling only supplies observations; task membership and map revisions never
invalidate retained geometry. Session archives remain owned by History.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import logging
import math
from typing import Any

from .const import MQTT_CUTTING_ACTIONS
from .zone_ledger import as_int, normalize_ledger_state

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1


def trail_store(hass: Any, entry_id: str) -> Any:
    from homeassistant.helpers.storage import Store

    key = f"navimower_vendor_trails_{entry_id}"
    try:
        return Store(hass, STORE_VERSION, key, serialize_in_event_loop=False)
    except TypeError:
        return Store(hass, STORE_VERSION, key)


class VendorTrailStore:
    """One sticky owner and cached SVG per (zone_id, ledger cycle_id)."""

    def __init__(self, hass: Any, entry_id: str, *, storage: Any = None) -> None:
        self.hass = hass
        self.storage = storage if storage is not None else trail_store(hass, entry_id)
        self.ledger = normalize_ledger_state(None)
        self.records: dict[int, dict[str, Any]] = {}
        self.revision = 0
        self._render_lock = asyncio.Lock()
        self._save_pending = False

    def export(self) -> dict[str, Any]:
        return deepcopy({"ledger": self.ledger, "records": self.records, "revision": self.revision})

    async def async_load(self) -> None:
        try:
            data = await self.storage.async_load()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Vendor trail restore failed", exc_info=True)
            return
        if not isinstance(data, dict):
            return
        self.ledger = normalize_ledger_state(data.get("ledger"))
        for key, row in (data.get("records") or {}).items():
            zone_id = as_int(key)
            ledger_row = self.ledger["zones"].get(str(zone_id)) or {}
            if zone_id and isinstance(row, dict) and row.get("cycle_id") == ledger_row.get("cycle_key"):
                self.records[zone_id] = deepcopy(row)
        self.revision = as_int(data.get("revision")) or 0

    def schedule_save(self) -> None:
        if self._save_pending:
            return
        self._save_pending = True

        def snapshot():
            self._save_pending = False
            return self.export()

        self.storage.async_delay_save(snapshot, 2)

    async def async_flush(self) -> None:
        await self.storage.async_save(self.export())

    def reconcile(self, ledger: dict[str, Any]) -> None:
        """Only a changed ledger cycle may revoke ownership and its old SVG."""
        for zone_id, row in list(self.records.items()):
            current = (ledger.get("zones") or {}).get(str(zone_id)) or {}
            cycle_id = current.get("cycle_key")
            if cycle_id and cycle_id != row.get("cycle_id"):
                del self.records[zone_id]
                self.revision += 1
        self.ledger = ledger

    def accept(self, row: dict[str, Any]) -> bool:
        """Accept nonempty current-cycle geometry without revoking last-good data."""
        zone_id = as_int(row.get("zone_id"))
        ledger_row = self.ledger["zones"].get(str(zone_id)) or {}
        cycle_id = ledger_row.get("cycle_key")
        points = row.get("points") or []
        if not cycle_id or len(points) < 2 or ledger_row.get("pending_vendor_cycle"):
            return False
        # A lagging compressed response is not an acknowledgement of a reset.
        # Keep its own timestamp (normalization must not relabel stale geometry).
        expected_start = as_int(ledger_row.get("vendor_start_time"))
        observed_start = as_int(row.get("geometry_start_time", row.get("start_time")))
        if expected_start and observed_start and expected_start != observed_start:
            return False
        if ledger_row.get("progress_pct") == 0 or ledger_row.get("progress_guard"):
            return False
        previous = self.records.get(zone_id) or {}
        if len(points) < len(previous.get("points") or []):
            return False
        digest = hashlib.sha256(json.dumps(points, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        if previous.get("geometry_revision") == digest:
            return False
        record = deepcopy(row)
        record.update({
            "cycle_id": cycle_id,
            "vendor_owned": True,
            "geometry_revision": digest,
            "artifact": previous.get("artifact"),
            "artifact_revision": previous.get("artifact_revision"),
            "artifact_anchor_xy": previous.get("artifact_anchor_xy"),
            "artifact_point_count": previous.get("artifact_point_count"),
            "tail": previous.get("tail", {}),
        })
        self.records[zone_id] = record
        self.revision += 1
        return True

    def owned_zone_ids(self) -> set[int]:
        return {zone_id for zone_id, row in self.records.items() if row.get("vendor_owned")}

    async def async_artifacts(
        self,
        width: float,
        *,
        build: bool = True,
        zone_ids: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        from .session_svg import SESSION_SVG_ARCHIVE_VERSION, build_session_svg_archive
        from .vendor_trail import build_vendor_render_source

        async with self._render_lock:
            if not build:
                return [deepcopy(row) for row in self.records.values()]
            selected = None if zone_ids is None else {int(zone) for zone in zone_ids}
            for zone_id, row in list(self.records.items()):
                if selected is not None and zone_id not in selected:
                    continue
                key = [row["cycle_id"], row["geometry_revision"], width, SESSION_SVG_ARCHIVE_VERSION]
                if row.get("artifact_revision") == key and row.get("artifact") is not None:
                    continue
                source = build_vendor_render_source([row], mowing_path_width_m=width)
                try:
                    artifact = await self.hass.async_add_executor_job(build_session_svg_archive, source)
                except Exception:  # noqa: BLE001
                    _LOGGER.warning("Vendor SVG generation failed for zone %s; keeping last good SVG", zone_id, exc_info=True)
                    continue
                if not isinstance(artifact, dict):
                    continue

                current = self.records.get(zone_id)
                if current is row:
                    target = row
                elif (
                    isinstance(current, dict)
                    and current.get("cycle_id") == row.get("cycle_id")
                    and current.get("artifact_revision") == row.get("artifact_revision")
                ):
                    # Geometry may advance while the executor is building this
                    # SVG. Within the same ZoneLedger cycle the completed SVG is
                    # still valid as a last-known-good prefix, so publish it to
                    # the newest row instead of throwing it away. The next call
                    # sees the newer geometry_revision and refreshes it again.
                    target = current
                else:
                    # A cycle reset/new cycle (or an independently newer
                    # artifact) invalidates this in-flight render.
                    continue

                target["artifact"] = artifact
                target["artifact_revision"] = key
                points = row.get("points") or []
                target["artifact_anchor_xy"] = (
                    [float(points[-1][0]), float(points[-1][1])]
                    if points
                    and isinstance(points[-1], (list, tuple))
                    and len(points[-1]) >= 2
                    else None
                )
                target["artifact_point_count"] = len(points)
                self.schedule_save()
            return [deepcopy(row) for row in self.records.values()]

    def update_live_tail(self, snapshot: dict[str, Any], session: dict[str, Any] | None) -> None:
        """Track only observed MQTT additions after adoption; trim on vendor advance.

        Timestamped samples prevent a changing spatial match from resurrecting
        old session geometry. No distance or point-count limit is applied.
        """
        if not session:
            return
        session_id = str(session.get("id") or session.get("sequence") or "")
        starts = set(session.get("segment_starts_ms") or [])
        points = session.get("points") or []
        for zone_id, row in self.records.items():
            state = row.setdefault("tail", {})
            segments = state.setdefault("segments", [])
            last_stamp = as_int(state.get("last_stamp")) or 0
            new_session = state.get("session_id") != session_id
            first_adoption = not state.get("initialized")
            split = new_session or bool(state.get("break_before_next"))
            for raw in points:
                if not isinstance(raw, list) or len(raw) < 8:
                    continue
                stamp = as_int(raw[0]) or 0
                if stamp <= last_stamp:
                    continue
                action = as_int(raw[6]) if len(raw) > 6 else None
                cutting = (
                    action in MQTT_CUTTING_ACTIONS
                    if action is not None
                    else str(raw[4]).lower() == "mowing"
                )
                if as_int(raw[7]) != zone_id or not cutting:
                    split = True
                    continue
                point = [stamp, float(raw[1]), float(raw[2])]
                if not all(math.isfinite(value) for value in point):
                    split = True
                    continue
                previous = segments[-1][-1] if segments else None
                if split or stamp in starts or previous is None or math.hypot(point[1]-previous[1], point[2]-previous[2]) > 5:
                    segments.append([])
                segments[-1].append(point)
                split = False
            state["break_before_next"] = split
            state.update({"session_id": session_id, "initialized": True,
                          "last_stamp": max([last_stamp] + [as_int(p[0]) or 0 for p in points if isinstance(p, list) and p])})
            revision = row.get("artifact_revision")
            artifact_revision = (
                revision[1]
                if isinstance(revision, (list, tuple)) and len(revision) >= 2
                else None
            )
            target = row.get("artifact_anchor_xy")
            anchor_changed = state.get("artifact_revision") != artifact_revision
            if (anchor_changed and artifact_revision and target) or first_adoption:
                match = None
                best = 0.75
                if artifact_revision and isinstance(target, (list, tuple)) and len(target) >= 2:
                    for si in range(len(segments)-1, -1, -1):
                        for pi in range(len(segments[si])-1, -1, -1):
                            p = segments[si][pi]
                            distance = math.hypot(
                                p[1] - float(target[0]),
                                p[2] - float(target[1]),
                            )
                            if distance < best:
                                best, match = distance, (si, pi)
                if match is not None:
                    si, pi = match
                    segments = [segments[si][pi:]] + segments[si+1:]
                elif first_adoption and artifact_revision:
                    # A restored/published base owns the earlier cycle. If its
                    # endpoint cannot be matched after a restart, never revive
                    # the whole MQTT session underneath that base.
                    segments = [[segments[-1][-1]]] if segments else []
                # With no base artifact the live layer owns the whole observed
                # session, so first adoption deliberately keeps all segments.
                state["artifact_revision"] = artifact_revision
            state["segments"] = segments

    def live_tail(self, zone_id: int) -> list[list[list[float]]]:
        row = self.records.get(zone_id) or {}
        return [[[p[1], p[2]] for p in segment] for segment in (row.get("tail") or {}).get("segments") or []]
