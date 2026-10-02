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
import statistics
import time
from typing import Any

from .const import MQTT_CUTTING_ACTIONS
from .zone_ledger import as_int, normalize_ledger_state
from .zone_segment_guard import (
    ZONE_SEGMENT_GUARD_VERSION,
    ZONE_SEGMENT_TOLERANCE_M,
    polygon_for_zone,
    polygon_signature,
    segment_within_zone_tolerance,
    zone_guard_break_indices,
)

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1
# Retained only as a fallback when map geometry is temporarily unavailable.
FUTURE_VENDOR_GAP_MIN_SPLIT_M = 15.0
FUTURE_VENDOR_GAP_MAX_SPLIT_M = 30.0
FUTURE_VENDOR_GAP_MEDIAN_MULTIPLIER = 6.0
FUTURE_VENDOR_GAP_GUARD_VERSION = ZONE_SEGMENT_GUARD_VERSION


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

        # Fresh installations start tracking at install time instead of adopting
        # an arbitrarily large vendor-retained current-cycle prefix. The baseline
        # is persisted so restarts can recover only geometry added afterwards.
        self.bootstrap_mode = "legacy_uninitialized"
        self.tracking_started_at_ms: int | None = None
        self.install_baselines: dict[int, dict[str, Any]] = {}
        self.preinstall_skipped_point_count = 0
        self.baseline_reanchor_count = 0
        self.recovery_checkpoint_at_ms: int | None = None
        self.recovery_count = 0
        self.last_recovery_backfill_seconds: float | None = None
        self.last_recovery_backfill_point_count = 0
        self.last_recovery_backfill_zone_count = 0
        self._restart_recovery_pending = False
        self._recovery_previous_checkpoint_ms: int | None = None
        self._batch_recovery = False
        self._batch_backfill_points = 0
        self._batch_backfill_zones: set[int] = set()
        self.last_vendor_fetch_candidate_zone_count = 0
        self.last_vendor_fetch_requested_zone_count = 0
        self.last_vendor_fetch_blocked_zone_count = 0

    def export(self) -> dict[str, Any]:
        return deepcopy(
            {
                "ledger": self.ledger,
                "records": self.records,
                "revision": self.revision,
                "recovery": {
                    "schema_version": 1,
                    "bootstrap_mode": self.bootstrap_mode,
                    "tracking_started_at_ms": self.tracking_started_at_ms,
                    "install_baselines": self.install_baselines,
                    "preinstall_skipped_point_count": self.preinstall_skipped_point_count,
                    "baseline_reanchor_count": self.baseline_reanchor_count,
                    "recovery_checkpoint_at_ms": self.recovery_checkpoint_at_ms,
                    "recovery_count": self.recovery_count,
                    "last_recovery_backfill_seconds": self.last_recovery_backfill_seconds,
                    "last_recovery_backfill_point_count": self.last_recovery_backfill_point_count,
                    "last_recovery_backfill_zone_count": self.last_recovery_backfill_zone_count,
                },
            }
        )

    async def async_load(self) -> None:
        now_ms = int(time.time() * 1000)
        try:
            data = await self.storage.async_load()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Vendor trail restore failed", exc_info=True)
            return
        if not isinstance(data, dict):
            self.bootstrap_mode = "from_install"
            self.tracking_started_at_ms = now_ms
            self.recovery_checkpoint_at_ms = now_ms
            # Persist the installation checkpoint immediately. Otherwise a HA
            # restart before the first successful trail poll could accidentally
            # turn the next startup into an unbounded first adoption.
            try:
                await self.storage.async_save(self.export())
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Vendor trail install checkpoint save failed", exc_info=True)
            return

        self.ledger = normalize_ledger_state(data.get("ledger"))
        for key, row in (data.get("records") or {}).items():
            zone_id = as_int(key)
            ledger_row = self.ledger["zones"].get(str(zone_id)) or {}
            if zone_id and isinstance(row, dict) and row.get("cycle_id") == ledger_row.get("cycle_key"):
                self.records[zone_id] = deepcopy(row)
        self.revision = as_int(data.get("revision")) or 0

        recovery = data.get("recovery")
        if isinstance(recovery, dict):
            self.bootstrap_mode = str(recovery.get("bootstrap_mode") or "existing_store")
            self.tracking_started_at_ms = as_int(recovery.get("tracking_started_at_ms"))
            self.preinstall_skipped_point_count = (
                as_int(recovery.get("preinstall_skipped_point_count")) or 0
            )
            self.baseline_reanchor_count = as_int(recovery.get("baseline_reanchor_count")) or 0
            self.recovery_checkpoint_at_ms = as_int(
                recovery.get("recovery_checkpoint_at_ms")
            )
            self.recovery_count = as_int(recovery.get("recovery_count")) or 0
            self.last_recovery_backfill_seconds = recovery.get(
                "last_recovery_backfill_seconds"
            )
            self.last_recovery_backfill_point_count = (
                as_int(recovery.get("last_recovery_backfill_point_count")) or 0
            )
            self.last_recovery_backfill_zone_count = (
                as_int(recovery.get("last_recovery_backfill_zone_count")) or 0
            )
            for key, row in (recovery.get("install_baselines") or {}).items():
                zone_id = as_int(key)
                if zone_id and isinstance(row, dict):
                    self.install_baselines[zone_id] = deepcopy(row)
        else:
            # Existing 0.4.5/0.4.6 stores are upgrades, not fresh installs.
            # Keep all their retained geometry and begin bounded recovery only
            # from this beta onward.
            self.bootstrap_mode = "existing_store_migration"
            self.tracking_started_at_ms = None
            self.recovery_checkpoint_at_ms = now_ms

        self._restart_recovery_pending = True
        self._recovery_previous_checkpoint_ms = self.recovery_checkpoint_at_ms

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
        """Mirror vendor-current cycle ownership into retained trail geometry.

        A confirmed cycle change still revokes the old geometry. In addition,
        fresh vendor current-state reset to 0% revokes the visible trail even if
        the vendor start timestamp has not changed yet. A small reset baseline
        prevents a lagging compressed endpoint from resurrecting the pre-reset
        prefix when mowing resumes.
        """
        for zone_id, row in list(self.records.items()):
            current = (ledger.get("zones") or {}).get(str(zone_id)) or {}
            cycle_id = current.get("cycle_key")
            progress = current.get("progress_pct")
            reset_to_zero = (
                progress is not None
                and float(progress) <= 0.0
                and not bool(current.get("stale"))
            )
            cycle_changed = bool(cycle_id and cycle_id != row.get("cycle_id"))
            if not (cycle_changed or reset_to_zero):
                continue

            if reset_to_zero and cycle_id:
                points = row.get("points") or []
                source_count = as_int(row.get("bootstrap_source_point_count"))
                if source_count is None:
                    source_count = len(points)
                anchor = row.get("artifact_anchor_xy")
                if not anchor and points:
                    anchor = deepcopy(points[-1][:2])
                self.install_baselines[zone_id] = {
                    "cycle_id": cycle_id,
                    "geometry_start_time": as_int(
                        row.get("geometry_start_time", row.get("start_time"))
                    ),
                    "source_point_offset": max(0, source_count or 0),
                    "anchor_xy": deepcopy(anchor),
                    "reason": "vendor_current_reset",
                }

            del self.records[zone_id]
            self.revision += 1

        for zone_id, baseline in list(self.install_baselines.items()):
            current = (ledger.get("zones") or {}).get(str(zone_id)) or {}
            cycle_id = current.get("cycle_key")
            if cycle_id and cycle_id != baseline.get("cycle_id"):
                del self.install_baselines[zone_id]
        self.ledger = ledger

    @staticmethod
    def _same_xy(left: Any, right: Any) -> bool:
        if (
            not isinstance(left, (list, tuple))
            or not isinstance(right, (list, tuple))
            or len(left) < 2
            or len(right) < 2
        ):
            return False
        try:
            return (
                abs(float(left[0]) - float(right[0])) <= 0.001
                and abs(float(left[1]) - float(right[1])) <= 0.001
            )
        except (TypeError, ValueError):
            return False

    def begin_vendor_observation_batch(self, observed_at_ms: int) -> None:
        """Start one successful vendor-trail poll/recovery accounting batch."""
        self._batch_recovery = self._restart_recovery_pending
        self._batch_backfill_points = 0
        self._batch_backfill_zones = set()
        if self._batch_recovery and self._recovery_previous_checkpoint_ms is None:
            self._recovery_previous_checkpoint_ms = self.recovery_checkpoint_at_ms

    def prepare_observation(
        self,
        row: dict[str, Any],
        *,
        observed_at_ms: int,
    ) -> dict[str, Any] | None:
        """Trim pre-install vendor geometry while preserving post-install growth."""
        zone_id = as_int(row.get("zone_id"))
        ledger_row = self.ledger["zones"].get(str(zone_id)) or {}
        cycle_id = ledger_row.get("cycle_key")
        points = row.get("points") or []
        if not zone_id or not cycle_id or not isinstance(points, list):
            return row

        source_count = len(points)
        baseline = self.install_baselines.get(zone_id)
        if baseline and baseline.get("cycle_id") != cycle_id:
            self.install_baselines.pop(zone_id, None)
            baseline = None

        if baseline is None and self.bootstrap_mode == "from_install":
            geometry_start = as_int(
                row.get("geometry_start_time", row.get("start_time"))
            )
            tracking_s = (
                int(self.tracking_started_at_ms / 1000)
                if self.tracking_started_at_ms is not None
                else None
            )
            # Missing/older vendor start metadata is treated conservatively:
            # establish a baseline instead of importing unknown pre-install history.
            preinstall_cycle = (
                geometry_start is None
                or tracking_s is None
                or geometry_start < tracking_s
            )
            if preinstall_cycle:
                anchor = deepcopy(points[-1][:2]) if points else None
                self.install_baselines[zone_id] = {
                    "cycle_id": cycle_id,
                    "geometry_start_time": geometry_start,
                    "source_point_offset": source_count,
                    "anchor_xy": anchor,
                }
                self.preinstall_skipped_point_count += source_count
                self.recovery_checkpoint_at_ms = observed_at_ms
                return None

        baseline = self.install_baselines.get(zone_id)
        prepared = deepcopy(row)
        if baseline is not None:
            anchor = baseline.get("anchor_xy")
            anchor_index: int | None = None
            offset = as_int(baseline.get("source_point_offset")) or 0
            if (
                offset > 0
                and offset <= source_count
                and self._same_xy(points[offset - 1], anchor)
            ):
                anchor_index = offset - 1
            elif anchor is not None:
                for index in range(source_count - 1, -1, -1):
                    if self._same_xy(points[index], anchor):
                        anchor_index = index
                        break

            if anchor_index is None:
                if baseline.get("reason") == "vendor_current_reset":
                    # A reset followed by a rewritten vendor geometry is exactly
                    # the new current route. Drop the old reset anchor and adopt
                    # the rewritten geometry instead of waiting for another poll.
                    self.install_baselines.pop(zone_id, None)
                    baseline = None
                else:
                    # Fresh-install vendor geometry was rewritten instead of
                    # appended. Never import the unknown pre-install prefix:
                    # move the baseline forward.
                    baseline["source_point_offset"] = source_count
                    baseline["anchor_xy"] = deepcopy(points[-1][:2]) if points else None
                    self.baseline_reanchor_count += 1
                    self.recovery_checkpoint_at_ms = observed_at_ms
                    return None

            if baseline is None:
                trimmed = deepcopy(points)
            else:
                trimmed = deepcopy(points[anchor_index:])
            if len(trimmed) < 2:
                self.recovery_checkpoint_at_ms = observed_at_ms
                return None
            prepared["points"] = trimmed
            prepared["point_count"] = len(trimmed)
            prepared["bootstrap_trimmed"] = True
            prepared["bootstrap_source_point_count"] = source_count
            prepared["bootstrap_source_offset"] = anchor_index

        previous = self.records.get(zone_id) or {}
        previous_source_count = as_int(previous.get("bootstrap_source_point_count"))
        if previous_source_count is None:
            previous_source_count = len(previous.get("points") or [])
        if baseline is not None and not previous:
            previous_source_count = as_int(baseline.get("source_point_offset")) or 0
        if self._batch_recovery:
            added = max(0, source_count - (previous_source_count or 0))
            if added:
                self._batch_backfill_points += added
                self._batch_backfill_zones.add(zone_id)

        prepared["bootstrap_source_point_count"] = source_count
        return prepared

    def finish_vendor_observation_batch(self, observed_at_ms: int) -> None:
        """Commit restart-recovery counters and advance the persistent checkpoint."""
        if self._batch_recovery:
            previous = self._recovery_previous_checkpoint_ms
            self.recovery_count += 1
            self.last_recovery_backfill_seconds = (
                round(max(0, observed_at_ms - previous) / 1000.0, 3)
                if previous is not None
                else None
            )
            self.last_recovery_backfill_point_count = self._batch_backfill_points
            self.last_recovery_backfill_zone_count = len(self._batch_backfill_zones)
            self._restart_recovery_pending = False
            self._recovery_previous_checkpoint_ms = None
        self.recovery_checkpoint_at_ms = observed_at_ms
        self._batch_recovery = False
        self._batch_backfill_points = 0
        self._batch_backfill_zones = set()

    def vendor_fetch_zone_ids(
        self,
        candidate_zone_ids: list[int],
        coverage: dict[int, dict[str, Any]],
    ) -> list[int]:
        """Return zones safe to request from vendor retained-trail transport.

        Fresh installs must not download a route prefix from a mowing cycle that
        already existed before tracking started. Existing beta6 baselines remain
        fetchable so upgrades preserve their restart-recovery contract.
        """
        candidates = sorted({int(zone_id) for zone_id in candidate_zone_ids if int(zone_id) > 0})
        self.last_vendor_fetch_candidate_zone_count = len(candidates)
        if self.bootstrap_mode != "from_install":
            self.last_vendor_fetch_requested_zone_count = len(candidates)
            self.last_vendor_fetch_blocked_zone_count = 0
            return candidates

        tracking_s = (
            int(self.tracking_started_at_ms / 1000)
            if self.tracking_started_at_ms is not None
            else None
        )
        requested: list[int] = []
        for zone_id in candidates:
            if zone_id in self.install_baselines or zone_id in self.records:
                requested.append(zone_id)
                continue
            row = coverage.get(zone_id)
            start_time = as_int(row.get("start_time")) if isinstance(row, dict) else None
            if (
                tracking_s is not None
                and start_time is not None
                and start_time > 0
                and start_time >= tracking_s
            ):
                requested.append(zone_id)

        self.last_vendor_fetch_requested_zone_count = len(requested)
        self.last_vendor_fetch_blocked_zone_count = len(candidates) - len(requested)
        return requested

    def recovery_diagnostics(self) -> dict[str, Any]:
        """Return privacy-safe fresh-install/restart recovery state."""
        return {
            "bootstrap_mode": self.bootstrap_mode,
            "tracking_started_at_ms": self.tracking_started_at_ms,
            "preinstall_baseline_zone_count": len(self.install_baselines),
            "preinstall_skipped_point_count": self.preinstall_skipped_point_count,
            "baseline_reanchor_count": self.baseline_reanchor_count,
            "recovery_checkpoint_at_ms": self.recovery_checkpoint_at_ms,
            "restart_recovery_pending": self._restart_recovery_pending,
            "recovery_count": self.recovery_count,
            "last_recovery_backfill_seconds": self.last_recovery_backfill_seconds,
            "last_recovery_backfill_point_count": self.last_recovery_backfill_point_count,
            "last_recovery_backfill_zone_count": self.last_recovery_backfill_zone_count,
            "last_vendor_fetch_candidate_zone_count": self.last_vendor_fetch_candidate_zone_count,
            "last_vendor_fetch_requested_zone_count": self.last_vendor_fetch_requested_zone_count,
            "last_vendor_fetch_blocked_zone_count": self.last_vendor_fetch_blocked_zone_count,
            "future_gap_guard_mode": "zone_polygon",
            "future_gap_guard_tolerance_m": ZONE_SEGMENT_TOLERANCE_M,
            "future_gap_guard_threshold_m": FUTURE_VENDOR_GAP_MIN_SPLIT_M,
            "future_gap_guard_max_threshold_m": FUTURE_VENDOR_GAP_MAX_SPLIT_M,
            "future_gap_guard_version": FUTURE_VENDOR_GAP_GUARD_VERSION,
            "future_gap_guard_break_count": sum(
                len(row.get("future_gap_break_indices") or [])
                for row in self.records.values()
                if isinstance(row, dict)
            ),
            "future_gap_guard_zone_count": sum(
                1
                for row in self.records.values()
                if isinstance(row, dict) and row.get("future_gap_break_indices")
            ),
        }

    @staticmethod
    def _gap_guard_threshold(points: list[Any]) -> float:
        """Return an outlier threshold that tolerates normal vendor compression."""
        distances: list[float] = []
        for first, second in zip(points, points[1:]):
            if (
                not isinstance(first, (list, tuple))
                or not isinstance(second, (list, tuple))
                or len(first) < 2
                or len(second) < 2
            ):
                continue
            try:
                distance = math.hypot(
                    float(second[0]) - float(first[0]),
                    float(second[1]) - float(first[1]),
                )
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(distance) and distance > 0:
                distances.append(distance)
        median = statistics.median(distances) if distances else 0.0
        return min(
            FUTURE_VENDOR_GAP_MAX_SPLIT_M,
            max(
                FUTURE_VENDOR_GAP_MIN_SPLIT_M,
                median * FUTURE_VENDOR_GAP_MEDIAN_MULTIPLIER,
            ),
        )

    @classmethod
    def _gap_breaks(cls, points: list[Any]) -> tuple[set[int], float]:
        threshold = cls._gap_guard_threshold(points)
        breaks: set[int] = set()
        for index in range(1, len(points)):
            first = points[index - 1]
            second = points[index]
            if (
                not isinstance(first, (list, tuple))
                or not isinstance(second, (list, tuple))
                or len(first) < 2
                or len(second) < 2
            ):
                continue
            try:
                distance = math.hypot(
                    float(second[0]) - float(first[0]),
                    float(second[1]) - float(first[1]),
                )
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(distance) and distance > threshold:
                breaks.add(index)
        return breaks, threshold

    def accept(self, row: dict[str, Any]) -> bool:
        """Accept all current-cycle vendor points without distance-based trimming."""
        zone_id = as_int(row.get("zone_id"))
        ledger_row = self.ledger["zones"].get(str(zone_id)) or {}
        cycle_id = ledger_row.get("cycle_key")
        points = row.get("points") or []
        if not cycle_id or len(points) < 2 or ledger_row.get("pending_vendor_cycle"):
            return False

        expected_start = as_int(ledger_row.get("vendor_start_time"))
        observed_start = as_int(row.get("geometry_start_time", row.get("start_time")))
        if expected_start and observed_start and expected_start != observed_start:
            return False
        if ledger_row.get("progress_pct") == 0 or ledger_row.get("progress_guard"):
            return False

        previous = self.records.get(zone_id) or {}
        previous_points = previous.get("points") or []
        if len(points) < len(previous_points):
            return False

        digest = hashlib.sha256(
            json.dumps(points, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()

        # v3 keeps the raw vendor sequence intact. Segment safety is evaluated
        # against the actual zone polygon only when a render/checkpoint is built.
        # This lets sparse compressed H-series geometry survive Dock/restart
        # without turning normal point spacing into permanent white holes.
        previous_version = as_int(previous.get("gap_guard_version")) or 0
        migrating_guard = bool(
            previous
            and previous_version < FUTURE_VENDOR_GAP_GUARD_VERSION
        )
        if previous.get("geometry_revision") == digest and not migrating_guard:
            return False

        record = deepcopy(row)
        record.update({
            "cycle_id": cycle_id,
            "vendor_owned": True,
            "geometry_revision": digest,
            "artifact": None if migrating_guard else previous.get("artifact"),
            "artifact_revision": None if migrating_guard else previous.get("artifact_revision"),
            "artifact_anchor_xy": previous.get("artifact_anchor_xy"),
            "artifact_point_count": previous.get("artifact_point_count"),
            "tail": previous.get("tail", {}),
            # Old distance-derived breaks are deliberately discarded. The next
            # artifact checkpoint recomputes only polygon-invalid edges.
            "future_gap_break_indices": [],
            "gap_guard_scanned_point_count": len(points),
            "gap_guard_threshold_m": None,
            "gap_guard_mode": "zone_polygon_pending",
            "gap_guard_tolerance_m": ZONE_SEGMENT_TOLERANCE_M,
            "gap_guard_version": FUTURE_VENDOR_GAP_GUARD_VERSION,
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
        map_zones: list[dict[str, Any]] | None = None,
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

                polygon = polygon_for_zone(map_zones or [], zone_id)
                render_row = deepcopy(row)
                if polygon:
                    breaks = zone_guard_break_indices(
                        row.get("points") or [],
                        polygon,
                        tolerance_m=ZONE_SEGMENT_TOLERANCE_M,
                    )
                    guard_mode = "zone_polygon"
                    guard_signature = polygon_signature(polygon) or "zone"
                    threshold = None
                else:
                    # Unit tests/cold startup without map geometry retain the
                    # previous conservative fallback. Runtime checkpoints pass
                    # map_zones and therefore do not use distance as policy.
                    breaks, threshold = self._gap_breaks(row.get("points") or [])
                    guard_mode = "adaptive_distance_fallback"
                    guard_signature = f"fallback:{round(threshold, 3)}"

                render_row["future_gap_break_indices"] = sorted(breaks)
                guard_key = (
                    f"{SESSION_SVG_ARCHIVE_VERSION}:"
                    f"zone_guard_v{FUTURE_VENDOR_GAP_GUARD_VERSION}:"
                    f"{guard_signature}"
                )
                key = [
                    row["cycle_id"],
                    row["geometry_revision"],
                    width,
                    guard_key,
                ]

                # Guard diagnostics are part of retained trail state, not a
                # by-product of a successful SVG. Persist them even when every
                # candidate edge is rejected and the renderer has no area to
                # publish yet.
                row["future_gap_break_indices"] = sorted(breaks)
                row["gap_guard_mode"] = guard_mode
                row["gap_guard_tolerance_m"] = (
                    ZONE_SEGMENT_TOLERANCE_M if polygon else None
                )
                row["gap_guard_threshold_m"] = (
                    None if threshold is None else round(threshold, 3)
                )
                row["gap_guard_version"] = FUTURE_VENDOR_GAP_GUARD_VERSION
                self.schedule_save()

                if row.get("artifact_revision") == key and row.get("artifact") is not None:
                    continue
                source = build_vendor_render_source(
                    [render_row],
                    mowing_path_width_m=width,
                )
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
                target["future_gap_break_indices"] = sorted(breaks)
                target["gap_guard_mode"] = guard_mode
                target["gap_guard_tolerance_m"] = (
                    ZONE_SEGMENT_TOLERANCE_M if polygon else None
                )
                target["gap_guard_threshold_m"] = (
                    None if threshold is None else round(threshold, 3)
                )
                target["gap_guard_version"] = FUTURE_VENDOR_GAP_GUARD_VERSION
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
        map_zones = ((snapshot.get("map") or {}).get("zones") or [])
        for zone_id, row in self.records.items():
            polygon = polygon_for_zone(map_zones, zone_id)
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
                polygon_break = bool(
                    previous is not None
                    and polygon
                    and not segment_within_zone_tolerance(
                        [previous[1], previous[2]],
                        [point[1], point[2]],
                        polygon,
                        tolerance_m=ZONE_SEGMENT_TOLERANCE_M,
                    )
                )
                if split or stamp in starts or previous is None or polygon_break:
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
            if (
                first_adoption
                and not artifact_revision
                and row.get("points")
            ):
                # Before the first checkpoint exists, retain the old adoption
                # safety: attach only at the vendor endpoint. Runtime queues one
                # initial checkpoint immediately after ownership is accepted.
                target = row["points"][-1]
            anchor_changed = state.get("artifact_revision") != artifact_revision
            if (anchor_changed and artifact_revision and target) or first_adoption:
                match = None
                best = 0.75
                if isinstance(target, (list, tuple)) and len(target) >= 2:
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
                elif first_adoption:
                    # Unknown pre-adoption history cannot become a full
                    # fallback, regardless of whether the first base is already
                    # restored or still being checkpointed.
                    segments = [[segments[-1][-1]]] if segments else []
                state["artifact_revision"] = artifact_revision
            state["segments"] = segments

    def live_tail(self, zone_id: int) -> list[list[list[float]]]:
        row = self.records.get(zone_id) or {}
        return [[[p[1], p[2]] for p in segment] for segment in (row.get("tail") or {}).get("segments") or []]
