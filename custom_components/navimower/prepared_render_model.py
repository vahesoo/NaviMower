"""Prepared backend render model for future Navimower Map Card consumers.

This module is deliberately presentation-adjacent but not presentation-owning:
the integration prepares stable geometry, layout metadata and live route SVG
paths while the frontend keeps colors, visibility, zoom, interaction and mower
artwork. Existing Map API/raw geometry remains authoritative and compatible.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from copy import deepcopy
import hashlib
import json
import math
import time
from typing import Any
from urllib.parse import quote

from .custom_area import OPT_CUSTOM_AREAS, parse_custom_areas
from .session_svg import split_session_route_segments
from .zone_state import simplify_xy_points

SCHEMA_VERSION = 1
VIEW_SIZE = 1000.0
LAYOUT_PADDING_RATIO = 0.05
LIVE_PREPARE_MIN_INTERVAL_SECONDS = 30.0
LIVE_TAIL_MAX_POINTS = 128
_ACTIVE_ACTIVITIES = {"mowing", "paused", "returning"}
_GEOREFERENCE_RENDER_KEYS = (
    "schema_version",
    "source",
    "status",
    "map_revision",
    "geodesy_model",
    "reference",
    "rotation_rad",
)


def _render_georeference(value: Any) -> dict[str, Any] | None:
    """Return only the local-map -> WGS84 transform consumed by renderers."""
    if not isinstance(value, dict):
        return None
    result = {
        key: deepcopy(value.get(key))
        for key in _GEOREFERENCE_RENDER_KEYS
        if value.get(key) is not None
    }
    reference = result.get("reference")
    if not isinstance(reference, dict) or result.get("rotation_rad") is None:
        return None
    return result


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    try:
        result = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return result


def _fmt(value: float) -> str:
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def _points(value: Any) -> list[list[float]]:
    if isinstance(value, dict):
        for key in ("polygon", "points", "path"):
            if key in value:
                return _points(value.get(key))
        return []
    if not isinstance(value, (list, tuple)):
        return []
    result: list[list[float]] = []
    for raw in value:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        x = _number(raw[0])
        y = _number(raw[1])
        if x is None or y is None:
            continue
        point = [x, y]
        if not result or result[-1] != point:
            result.append(point)
    return result


def _polygon_points(value: Any) -> list[list[float]]:
    result = _points(value)
    if len(result) >= 2 and result[0] == result[-1]:
        result.pop()
    return result if len(result) >= 3 else []


def _path_d(points: list[list[float]], *, closed: bool) -> str:
    if len(points) < (3 if closed else 2):
        return ""
    commands = [f"M{_fmt(points[0][0])} {_fmt(points[0][1])}"]
    commands.extend(f"L{_fmt(point[0])} {_fmt(point[1])}" for point in points[1:])
    if closed:
        commands.append("Z")
    return "".join(commands)


def _bounds(points: list[list[float]]) -> list[float] | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _centroid(points: list[list[float]]) -> list[float] | None:
    if len(points) < 3:
        return None
    cross_sum = 0.0
    x_sum = 0.0
    y_sum = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        cross = x1 * y2 - x2 * y1
        cross_sum += cross
        x_sum += (x1 + x2) * cross
        y_sum += (y1 + y2) * cross
    if abs(cross_sum) <= 1e-9:
        return [
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        ]
    return [x_sum / (3.0 * cross_sum), y_sum / (3.0 * cross_sum)]


def _prepared_polygon(
    raw: Any,
    *,
    identity: Any = None,
    name: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    points = _polygon_points(raw)
    if not points:
        return None
    row: dict[str, Any] = {
        "path_d": _path_d(points, closed=True),
        "bounds": _bounds(points),
        "centroid": _centroid(points),
        "point_count": len(points),
    }
    if identity is not None:
        row["id"] = identity
    if name not in (None, ""):
        row["name"] = str(name)
    if extra:
        row.update(extra)
    return row


def _prepared_line(
    raw: Any,
    *,
    identity: Any = None,
    name: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    points = _points(raw)
    if len(points) < 2:
        return None
    row: dict[str, Any] = {
        "path_d": _path_d(points, closed=False),
        "bounds": _bounds(points),
        "point_count": len(points),
    }
    if identity is not None:
        row["id"] = identity
    if name not in (None, ""):
        row["name"] = str(name)
    if extra:
        row.update(extra)
    return row


def _gate_polygon(raw: Any) -> list[list[float]]:
    polygon = _polygon_points(raw)
    if polygon:
        return polygon
    if not isinstance(raw, dict):
        return []
    x_min = _number(raw.get("x_min"))
    x_max = _number(raw.get("x_max"))
    y_min = _number(raw.get("y_min"))
    y_max = _number(raw.get("y_max"))
    if None in (x_min, x_max, y_min, y_max):
        return []
    left, right = sorted((float(x_min), float(x_max)))
    bottom, top = sorted((float(y_min), float(y_max)))
    if left == right or bottom == top:
        return []
    return [[left, bottom], [right, bottom], [right, top], [left, top]]


def _layout(points: list[list[float]]) -> dict[str, Any] | None:
    if not points:
        return None
    raw = _bounds(points)
    if raw is None:
        return None
    min_x, min_y, max_x, max_y = raw
    span_x = max(max_x - min_x, 0.1)
    span_y = max(max_y - min_y, 0.1)
    min_x -= span_x * LAYOUT_PADDING_RATIO
    max_x += span_x * LAYOUT_PADDING_RATIO
    min_y -= span_y * LAYOUT_PADDING_RATIO
    max_y += span_y * LAYOUT_PADDING_RATIO
    span_x = max_x - min_x
    span_y = max_y - min_y
    scale = min(VIEW_SIZE / span_x, VIEW_SIZE / span_y)
    offset_x = (VIEW_SIZE - span_x * scale) / 2.0
    offset_y = (VIEW_SIZE - span_y * scale) / 2.0
    translate_x = offset_x - min_x * scale
    translate_y = offset_y + max_y * scale
    return {
        "view_size": VIEW_SIZE,
        "padding_ratio": LAYOUT_PADDING_RATIO,
        "raw_bounds": raw,
        "padded_bounds": [min_x, min_y, max_x, max_y],
        "span": [span_x, span_y],
        "scale": scale,
        "offset": [offset_x, offset_y],
        # SVG matrix(a b c d e f): screen Y is inverted exactly once.
        "matrix": [scale, 0.0, 0.0, -scale, translate_x, translate_y],
    }


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _encode_resource(
    kind: str,
    payload: dict[str, Any],
    entry_id: str,
) -> dict[str, Any]:
    body = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    resource_id = hashlib.sha256(
        b"navimower-prepared-render-model:"
        + str(SCHEMA_VERSION).encode()
        + b":"
        + entry_id.encode()
        + b":"
        + kind.encode()
        + b":"
        + body
    ).hexdigest()
    query_key = "static_render_model" if kind == "static" else "live_route_render"
    return {
        "kind": kind,
        "resource_id": resource_id,
        "body": body,
        "descriptor": {
            "resource_id": resource_id,
            "format": "json",
            "scope": f"{kind}_render_model",
            "coordinate_space": "map_xy_m",
            "byte_length": len(body),
            "url": (
                f"/api/navimower/map/{quote(entry_id, safe='')}"
                f"?{query_key}={resource_id}"
            ),
        },
    }


def build_static_render_model(source: dict[str, Any]) -> dict[str, Any]:
    """Prepare style-independent map geometry and card-equivalent layouts."""
    map_data = source.get("map") if isinstance(source.get("map"), dict) else {}
    zones: list[dict[str, Any]] = []
    off_limits: list[dict[str, Any]] = []
    vf_off: list[dict[str, Any]] = []
    channels: list[dict[str, Any]] = []
    gate_areas: list[dict[str, Any]] = []
    custom_areas: list[dict[str, Any]] = []

    stable_without_gates: list[list[float]] = []
    stable_with_gates: list[list[float]] = []
    invalid = 0
    source_points = 0
    prepared_points = 0

    for raw in map_data.get("zones") or []:
        if not isinstance(raw, dict):
            invalid += 1
            continue
        points = _polygon_points(raw.get("polygon"))
        source_points += len(points)
        row = _prepared_polygon(
            points,
            identity=_integer(raw.get("id")),
            name=raw.get("name"),
            extra={
                "area_m2": _number(raw.get("area")),
            },
        )
        if row is None:
            invalid += 1
            continue
        zones.append(row)
        prepared_points += row["point_count"]
        stable_without_gates.extend(points)

    for index, raw in enumerate(map_data.get("off_limit_areas") or []):
        points = _polygon_points(raw)
        source_points += len(points)
        row = _prepared_polygon(points, identity=index)
        if row is None:
            invalid += 1
            continue
        off_limits.append(row)
        prepared_points += row["point_count"]
        stable_without_gates.extend(points)

    for index, raw in enumerate(map_data.get("vf_off_areas") or []):
        points = _polygon_points(raw)
        source_points += len(points)
        row = _prepared_polygon(points, identity=index)
        if row is None:
            invalid += 1
            continue
        vf_off.append(row)
        prepared_points += row["point_count"]
        stable_without_gates.extend(points)

    for index, raw in enumerate(map_data.get("channels") or []):
        if not isinstance(raw, dict):
            invalid += 1
            continue
        points = _points(raw.get("points"))
        source_points += len(points)
        row = _prepared_line(
            points,
            identity=raw.get("id", index),
            name=raw.get("name"),
            extra={
                "connection": deepcopy(raw.get("connection") or []),
                "tunnel_type": raw.get("tunnel_type"),
            },
        )
        if row is None:
            invalid += 1
            continue
        channels.append(row)
        prepared_points += row["point_count"]
        stable_without_gates.extend(points)

    stable_with_gates.extend(stable_without_gates)
    for index, raw in enumerate(source.get("gate_areas") or []):
        points = _gate_polygon(raw)
        source_points += len(points)
        row = _prepared_polygon(
            points,
            identity=(raw.get("id") if isinstance(raw, dict) else index),
            name=(raw.get("name") if isinstance(raw, dict) else None),
        )
        if row is None:
            invalid += 1
            continue
        gate_areas.append(row)
        prepared_points += row["point_count"]
        stable_with_gates.extend(points)

    for index, raw in enumerate(source.get("custom_areas") or []):
        if not isinstance(raw, dict):
            invalid += 1
            continue
        points = _polygon_points(raw.get("polygon"))
        source_points += len(points)
        row = _prepared_polygon(
            points,
            identity=raw.get("id", index),
            name=raw.get("name"),
            extra={"source": raw.get("source")},
        )
        if row is None:
            invalid += 1
            continue
        custom_areas.append(row)
        prepared_points += row["point_count"]

    station = map_data.get("station")
    prepared_station = None
    if isinstance(station, dict):
        x = _number(station.get("x"))
        y = _number(station.get("y"))
        if x is not None and y is not None:
            prepared_station = {
                key: deepcopy(station.get(key))
                for key in (
                    "x",
                    "y",
                    "direction",
                    "width",
                    "length",
                    "center_offset",
                    "nav_pos",
                )
                if key in station
            }
            stable_without_gates.append([x, y])
            stable_with_gates.append([x, y])
        elif station:
            invalid += 1

    without_gate_layout = _layout(stable_without_gates)
    with_gate_layout = _layout(stable_with_gates)
    counts = {
        "zones": len(zones),
        "off_limit_areas": len(off_limits),
        "vf_off_areas": len(vf_off),
        "channels": len(channels),
        "gate_areas": len(gate_areas),
        "custom_areas": len(custom_areas),
        "source_point_count": source_points,
        "prepared_point_count": prepared_points,
        "invalid_geometry_count": invalid,
    }
    expected_counts = {
        "zones": sum(isinstance(row, dict) and len(_polygon_points(row.get("polygon"))) >= 3 for row in map_data.get("zones") or []),
        "off_limit_areas": sum(len(_polygon_points(row)) >= 3 for row in map_data.get("off_limit_areas") or []),
        "vf_off_areas": sum(len(_polygon_points(row)) >= 3 for row in map_data.get("vf_off_areas") or []),
        "channels": sum(isinstance(row, dict) and len(_points(row.get("points"))) >= 2 for row in map_data.get("channels") or []),
        "gate_areas": sum(len(_gate_polygon(row)) >= 3 for row in source.get("gate_areas") or []),
        "custom_areas": sum(isinstance(row, dict) and len(_polygon_points(row.get("polygon"))) >= 3 for row in source.get("custom_areas") or []),
    }
    count_parity = all(
        counts[key] == expected_counts[key]
        for key in expected_counts
    )
    point_parity = source_points == prepared_points
    parity = count_parity and point_parity

    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "static_map_render_model",
        "coordinate_space": "map_xy_m",
        "map_revision": map_data.get("revision"),
        "map_version": map_data.get("map_version"),
        "map_modified_count": map_data.get("modified_count"),
        "map_metadata": {
            "id": map_data.get("id"),
            "map_id": map_data.get("map_id"),
            "map_base_id": map_data.get("map_base_id"),
            "name": map_data.get("name"),
            "area_m2": _number(map_data.get("area")),
            "width_m": _number(map_data.get("width")),
            "height_m": _number(map_data.get("height")),
            "north_offset": _number(map_data.get("north_offset")),
            "version": map_data.get("version"),
        },
        "georeference": _render_georeference(map_data.get("georeference")),
        "layout": {
            "without_gate_areas": without_gate_layout,
            "with_gate_areas": with_gate_layout,
        },
        "layers": {
            "zones": zones,
            "off_limit_areas": off_limits,
            "vf_off_areas": vf_off,
            "channels": channels,
            "gate_areas": gate_areas,
            "custom_areas": custom_areas,
        },
        "station": prepared_station,
        "geometry_summary": {
            **counts,
            "expected_counts": expected_counts,
            "count_parity_ok": count_parity,
            "point_parity_ok": point_parity,
            "parity_ok": parity,
        },
    }


def build_live_route_render_model(source: dict[str, Any]) -> dict[str, Any]:
    """Prepare current route as SVG-ready paths without changing route ownership."""
    rows: list[dict[str, Any]] = []
    total_points = 0
    all_points: list[list[float]] = []
    invalid = 0
    for index, raw in enumerate(source.get("trail_segments") or []):
        points = _points(raw)
        if len(points) < 2:
            if raw:
                invalid += 1
            continue
        row = _prepared_line(points, identity=index)
        if row is None:
            invalid += 1
            continue
        rows.append(row)
        total_points += row["point_count"]
        all_points.extend(points)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "live_route_render_model",
        "coordinate_space": "map_xy_m",
        "trail_session": source.get("trail_session"),
        "trail_active": bool(source.get("trail_active")),
        "activity": source.get("activity"),
        "current_physical_zone_id": source.get("current_physical_zone_id"),
        "segments": rows,
        "bounds": _bounds(all_points),
        "segment_count": len(rows),
        "point_count": total_points,
        "invalid_segment_count": invalid,
    }


class PreparedRenderModelManager:
    """Prewarm static geometry and live route render resources for one mower."""

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self.entry_id = str(coordinator.entry.entry_id)
        self._unsub = None
        self._closed = False
        self._static_task: asyncio.Task | None = None
        self._live_task: asyncio.Task | None = None
        self._live_timer: asyncio.TimerHandle | None = None
        self._static_key: str | None = None
        self._live_key: tuple[Any, ...] | None = None
        self._static_pending = False
        self._live_pending = False
        self._static_resources: list[dict[str, Any]] = []
        self._live_resources: list[dict[str, Any]] = []
        self._last_live_build_mono: float | None = None
        self._live_base_resource_id: str | None = None
        self._live_base_trail_session: Any = None
        self._live_base_segment_point_counts: dict[int, int] = {}
        self._live_base_point_count = 0

        self.static_build_count = 0
        self.live_build_count = 0
        self.static_unchanged_count = 0
        self.live_unchanged_count = 0
        self.static_publication_revision = 0
        self.live_publication_revision = 0
        self.coalesced_static_updates = 0
        self.coalesced_live_updates = 0
        self.failure_count = 0
        self.static_failure_count = 0
        self.live_failure_count = 0
        self.last_static_build_ms: float | None = None
        self.last_live_build_ms: float | None = None
        self.last_error: str | None = None
        self.last_static_error: str | None = None
        self.last_live_error: str | None = None
        self.last_static_summary: dict[str, Any] | None = None
        self.last_live_summary: dict[str, Any] | None = None
        self.manifest_reads = 0
        self.static_resource_reads = 0
        self.live_resource_reads = 0
        self.static_resource_bytes_served_total = 0
        self.live_resource_bytes_served_total = 0
        self.static_resource_not_modified_count = 0
        self.live_resource_not_modified_count = 0
        self._manifest_first_read_mono: float | None = None
        self._manifest_last_read_mono: float | None = None
        self._static_first_read_mono: float | None = None
        self._static_last_read_mono: float | None = None
        self._live_first_read_mono: float | None = None
        self._live_last_read_mono: float | None = None
        self.live_tail_requests = 0
        self.live_tail_success_count = 0
        self.live_tail_full_fallback_count = 0
        self.live_tail_max_points_observed = 0
        self.last_live_tail_point_count = 0
        self.last_live_tail_base_point_count = 0
        self.last_live_tail_current_point_count = 0
        self.last_live_tail_reason: str | None = None

    def start(self) -> None:
        if self._closed or self._unsub is not None:
            return
        self._unsub = self.coordinator.async_add_listener(self._state_updated)
        self.request_refresh(force_live=True)

    def _state_updated(self) -> None:
        self.request_refresh()

    def _gate_areas(self) -> list[dict[str, Any]]:
        return [
            item.as_dict()
            for item in getattr(self.coordinator, "channels", []) or []
            if hasattr(item, "as_dict")
        ]

    def _custom_areas(self) -> list[dict[str, Any]]:
        return [
            item.as_dict()
            for item in parse_custom_areas(
                self.coordinator.entry.options.get(OPT_CUSTOM_AREAS)
            )
        ]

    def _static_source(self) -> dict[str, Any]:
        """Return exactly the presentation-stable inputs used by the static model."""
        data = self.coordinator.data or {}
        raw = data.get("map") if isinstance(data.get("map"), dict) else {}
        map_data = {
            key: deepcopy(raw.get(key))
            for key in (
                "revision",
                "map_version",
                "modified_count",
                "id",
                "map_id",
                "map_base_id",
                "name",
                "area",
                "width",
                "height",
                "north_offset",
                "version",
                "zones",
                "off_limit_areas",
                "vf_off_areas",
                "channels",
                "station",
            )
            if raw.get(key) is not None
        }
        map_data["georeference"] = _render_georeference(raw.get("georeference"))
        return {
            "map": map_data,
            "gate_areas": deepcopy(self._gate_areas()),
            "custom_areas": deepcopy(self._custom_areas()),
        }

    def _static_signature(self) -> str:
        return _json_hash(self._static_source())

    def _live_signature(self) -> tuple[Any, ...]:
        data = self.coordinator.data or {}
        history = getattr(self.coordinator, "history", None)
        store = getattr(self.coordinator, "vendor_trail_store", None)
        return (
            str(data.get("activity") or "").lower(),
            getattr(history, "active_session_no", None),
            getattr(history, "trail_revision", None),
            getattr(store, "revision", None),
            data.get("current_physical_zone_id"),
            bool(data.get("trail_active")),
        )

    def request_refresh(self, *, force_live: bool = False) -> None:
        if self._closed:
            return

        static_key = self._static_signature()
        if static_key != self._static_key:
            self._static_key = static_key
            if self._static_task is not None and not self._static_task.done():
                self._static_pending = True
                self.coalesced_static_updates += 1
            else:
                self._start_static_build()

        live_key = self._live_signature()
        previous_live_key = self._live_key
        if not force_live and live_key == previous_live_key:
            return
        self._live_key = live_key
        activity = str((self.coordinator.data or {}).get("activity") or "").lower()
        critical_change = (
            previous_live_key is None
            or live_key[0] != previous_live_key[0]
            or live_key[1] != previous_live_key[1]
            or live_key[4] != previous_live_key[4]
            or live_key[5] != previous_live_key[5]
        )
        immediate = (
            force_live
            or critical_change
            or activity not in _ACTIVE_ACTIVITIES
            or self._last_live_build_mono is None
            or time.monotonic() - self._last_live_build_mono
            >= LIVE_PREPARE_MIN_INTERVAL_SECONDS
        )
        if immediate:
            if self._live_timer is not None:
                self._live_timer.cancel()
                self._live_timer = None
            self._queue_live_build()
            return
        if self._live_timer is None:
            remaining = max(
                0.0,
                LIVE_PREPARE_MIN_INTERVAL_SECONDS
                - (time.monotonic() - self._last_live_build_mono),
            )
            self._live_timer = self.hass.loop.call_later(
                remaining,
                self._live_timer_fired,
            )
        else:
            self.coalesced_live_updates += 1

    def _live_timer_fired(self) -> None:
        self._live_timer = None
        if not self._closed:
            self._queue_live_build()

    def _task(self, coroutine, name: str) -> asyncio.Task:
        factory = getattr(self.hass, "async_create_background_task", None)
        if factory is not None:
            return factory(coroutine, name, eager_start=False)
        return asyncio.create_task(coroutine)

    def _start_static_build(self) -> None:
        if self._closed:
            return
        self._static_task = self._task(
            self._build_static(),
            f"Navimower prepared static render {self.entry_id}",
        )

    def _queue_live_build(self) -> None:
        if self._closed:
            return
        if self._live_task is not None and not self._live_task.done():
            self._live_pending = True
            self.coalesced_live_updates += 1
            return
        self._live_task = self._task(
            self._build_live(),
            f"Navimower prepared live route {self.entry_id}",
        )

    async def _build_static(self) -> None:
        try:
            started = time.perf_counter()
            source = self._static_source()
            model = await self.hass.async_add_executor_job(
                build_static_render_model,
                source,
            )
            resource = await self.hass.async_add_executor_job(
                _encode_resource,
                "static",
                model,
                self.entry_id,
            )
            self.static_build_count += 1
            self.last_static_build_ms = round(
                (time.perf_counter() - started) * 1000.0,
                2,
            )
            summary = deepcopy(model.get("geometry_summary") or {})
            summary["resource_id"] = resource["resource_id"]
            summary["byte_length"] = len(resource["body"])
            self.last_static_summary = summary
            current = self._static_resources[0] if self._static_resources else None
            if current and current["resource_id"] == resource["resource_id"]:
                self.static_unchanged_count += 1
            else:
                self._static_resources = [
                    resource,
                    *[
                        item
                        for item in self._static_resources
                        if item["resource_id"] != resource["resource_id"]
                    ],
                ][:2]
                self.static_publication_revision += 1
            self.last_static_error = None
            self.last_error = self.last_live_error
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - optional prepared resource
            self.failure_count += 1
            self.static_failure_count += 1
            self.last_static_error = type(err).__name__
            self.last_error = self.last_static_error
            self._static_key = None
        finally:
            self._static_task = None
            if self._static_pending and not self._closed:
                self._static_pending = False
                self._start_static_build()

    def _live_source(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        payload = self.coordinator._map_payload_with_sessions([], None)  # noqa: SLF001
        return {
            "trail_segments": deepcopy(payload.get("trail_segments") or []),
            "trail_session": payload.get("trail_session"),
            "trail_active": bool(payload.get("trail_active")),
            "activity": data.get("activity"),
            "current_physical_zone_id": data.get("current_physical_zone_id"),
        }

    async def _build_live(self) -> None:
        try:
            started = time.perf_counter()
            source = self._live_source()
            model = await self.hass.async_add_executor_job(
                build_live_route_render_model,
                source,
            )
            resource = await self.hass.async_add_executor_job(
                _encode_resource,
                "live",
                model,
                self.entry_id,
            )
            self.live_build_count += 1
            self._last_live_build_mono = time.monotonic()
            self.last_live_build_ms = round(
                (time.perf_counter() - started) * 1000.0,
                2,
            )
            self.last_live_summary = {
                "resource_id": resource["resource_id"],
                "byte_length": len(resource["body"]),
                "segment_count": model.get("segment_count"),
                "point_count": model.get("point_count"),
                "invalid_segment_count": model.get("invalid_segment_count"),
                "trail_session": model.get("trail_session"),
                "trail_active": model.get("trail_active"),
                "activity": model.get("activity"),
            }
            self._live_base_resource_id = resource["resource_id"]
            self._live_base_trail_session = model.get("trail_session")
            self._live_base_segment_point_counts = {
                int(row.get("id")): int(row.get("point_count") or 0)
                for row in model.get("segments") or []
                if _integer(row.get("id")) is not None
                and _integer(row.get("point_count")) is not None
            }
            self._live_base_point_count = int(model.get("point_count") or 0)
            current = self._live_resources[0] if self._live_resources else None
            if current and current["resource_id"] == resource["resource_id"]:
                self.live_unchanged_count += 1
            else:
                self._live_resources = [
                    resource,
                    *[
                        item
                        for item in self._live_resources
                        if item["resource_id"] != resource["resource_id"]
                    ],
                ][:2]
                self.live_publication_revision += 1
            self.last_live_error = None
            self.last_error = self.last_static_error
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - optional prepared resource
            self.failure_count += 1
            self.live_failure_count += 1
            self.last_live_error = type(err).__name__
            self.last_error = self.last_live_error
            self._live_key = None
        finally:
            self._live_task = None
            if self._live_pending and not self._closed:
                self._live_pending = False
                self.request_refresh(force_live=True)

    def discovery(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "scope": "prepared_map_render_model",
            "coordinate_space": "map_xy_m",
            "manifest_url": (
                f"/api/navimower/map/{quote(self.entry_id, safe='')}"
                "?render_model_manifest=1"
            ),
            "ready_only": True,
            "live_route_min_interval_s": LIVE_PREPARE_MIN_INTERVAL_SECONDS,
            "live_tail_max_points": LIVE_TAIL_MAX_POINTS,
            "history_manifest_url": (
                f"/api/navimower/history-manifest/{quote(self.entry_id, safe='')}"
            ),
            "history_resource_url_template": (
                f"/api/navimower/history-resource/{quote(self.entry_id, safe='')}/"
                "{resource_id}"
            ),
            "capabilities": {
                "static_svg_paths": True,
                "card_equivalent_layout": True,
                "live_route_svg_paths": True,
                "live_route_short_tail": True,
                "live_route_tail_only_query": True,
                "current_cycle_zone_resources": True,
                "history_render_archive": True,
                "history_ready_manifest": True,
                "history_content_addressed_resources": True,
                "history_etag": True,
                "style_independent": True,
            },
        }

    def manifest(self) -> dict[str, Any]:
        self.manifest_reads += 1
        now = time.monotonic()
        if self._manifest_first_read_mono is None:
            self._manifest_first_read_mono = now
        self._manifest_last_read_mono = now
        self.request_refresh()
        static = self._static_resources[0]["descriptor"] if self._static_resources else None
        live = self._live_resources[0]["descriptor"] if self._live_resources else None
        entry = quote(self.entry_id, safe="")
        return {
            "schema_version": SCHEMA_VERSION,
            "scope": "prepared_map_render_model",
            "entry_id": self.entry_id,
            "coordinate_space": "map_xy_m",
            "building": {
                "static": bool(self._static_task and not self._static_task.done()),
                "live_route": bool(self._live_task and not self._live_task.done())
                or self._live_timer is not None,
            },
            "publication_revision": {
                "static": self.static_publication_revision,
                "live_route": self.live_publication_revision,
            },
            "live_route_min_interval_s": LIVE_PREPARE_MIN_INTERVAL_SECONDS,
            "live_tail_max_points": LIVE_TAIL_MAX_POINTS,
            "static": deepcopy(static),
            "live_route": deepcopy(live),
            "current_cycle_manifest_url": (
                f"/api/navimower/map/{entry}?artifacts_only=1"
            ),
            "history_index_url": f"/api/navimower/sessions/{entry}",
            "history_manifest_url": (
                f"/api/navimower/history-manifest/{entry}"
            ),
            "history_resource_url_template": (
                f"/api/navimower/history-resource/{entry}/{{resource_id}}"
            ),
            "session_render_url_template": (
                f"/api/navimower/session-render/{entry}/{{session_id}}"
            ),
            "capabilities": self.discovery()["capabilities"],
        }

    def resource(
        self,
        kind: str,
        resource_id: str,
    ) -> dict[str, Any] | None:
        resources = self._static_resources if kind == "static" else self._live_resources
        resource = next(
            (item for item in resources if item["resource_id"] == resource_id),
            None,
        )
        if resource is not None:
            now = time.monotonic()
            if kind == "static":
                self.static_resource_reads += 1
                if self._static_first_read_mono is None:
                    self._static_first_read_mono = now
                self._static_last_read_mono = now
            else:
                self.live_resource_reads += 1
                if self._live_first_read_mono is None:
                    self._live_first_read_mono = now
                self._live_last_read_mono = now
        return resource

    def record_resource_response(
        self,
        kind: str,
        *,
        byte_length: int = 0,
        not_modified: bool = False,
    ) -> None:
        """Track actual prepared resource response bytes for field diagnostics."""
        if kind == "static":
            self.static_resource_bytes_served_total += max(0, int(byte_length))
            if not_modified:
                self.static_resource_not_modified_count += 1
            return
        self.live_resource_bytes_served_total += max(0, int(byte_length))
        if not_modified:
            self.live_resource_not_modified_count += 1

    @staticmethod
    def _age_seconds(value: float | None) -> float | None:
        if value is None:
            return None
        return round(max(0.0, time.monotonic() - value), 1)

    def live_tail_payload(
        self,
        trail_segments: Any,
        trail_session: Any,
    ) -> dict[str, Any]:
        """Return only points added after the latest prepared live resource."""
        self.live_tail_requests += 1
        raw_segments = trail_segments if isinstance(trail_segments, list) else []
        clean_segments = [_points(raw) for raw in raw_segments]
        current_point_count = sum(len(points) for points in clean_segments)
        base_resource_id = self._live_base_resource_id
        base_point_count = self._live_base_point_count
        result: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "scope": "prepared_live_tail",
            "coordinate_space": "map_xy_m",
            "usable": False,
            "base_resource_id": base_resource_id,
            "trail_session": trail_session,
            "base_point_count": base_point_count,
            "current_point_count": current_point_count,
            "point_count": 0,
            "segment_count": 0,
            "max_points": LIVE_TAIL_MAX_POINTS,
            "segments": [],
            "reason": None,
        }

        reason: str | None = None
        if not base_resource_id:
            reason = "prepared_live_unavailable"
        elif (
            self._live_base_trail_session is not None
            and trail_session is not None
            and str(self._live_base_trail_session) != str(trail_session)
        ):
            reason = "trail_session_mismatch"
        elif base_point_count > current_point_count:
            reason = "trail_rewound"

        tail: list[list[list[float]]] = []
        if reason is None:
            for index, points in enumerate(clean_segments):
                if len(points) < 2:
                    continue
                base_count = max(
                    0,
                    int(self._live_base_segment_point_counts.get(index, 0)),
                )
                if base_count > len(points):
                    reason = "trail_rewound"
                    break
                if base_count == 0:
                    piece = points
                elif len(points) > base_count:
                    piece = points[max(0, base_count - 1):]
                else:
                    piece = []
                if len(piece) >= 2:
                    tail.append(piece)

        tail_point_count = sum(len(points) for points in tail)
        if reason is None and tail_point_count > LIVE_TAIL_MAX_POINTS:
            reason = "tail_limit_exceeded"

        self.last_live_tail_base_point_count = base_point_count
        self.last_live_tail_current_point_count = current_point_count
        self.last_live_tail_point_count = tail_point_count
        self.live_tail_max_points_observed = max(
            self.live_tail_max_points_observed,
            tail_point_count,
        )
        self.last_live_tail_reason = reason

        if reason is not None:
            self.live_tail_full_fallback_count += 1
            result["reason"] = reason
            return result

        self.live_tail_success_count += 1
        result.update(
            {
                "usable": True,
                "point_count": tail_point_count,
                "segment_count": len(tail),
                "segments": deepcopy(tail),
            }
        )
        return result

    def diagnostics(self) -> dict[str, Any]:
        static = self._static_resources[0] if self._static_resources else None
        live = self._live_resources[0] if self._live_resources else None
        return {
            "schema_version": SCHEMA_VERSION,
            "prewarm_started": self._unsub is not None and not self._closed,
            "live_prepare_min_interval_s": LIVE_PREPARE_MIN_INTERVAL_SECONDS,
            "static_build_count": self.static_build_count,
            "live_build_count": self.live_build_count,
            "static_unchanged_count": self.static_unchanged_count,
            "live_unchanged_count": self.live_unchanged_count,
            "coalesced_static_updates": self.coalesced_static_updates,
            "coalesced_live_updates": self.coalesced_live_updates,
            "failure_count": self.failure_count,
            "static_failure_count": self.static_failure_count,
            "live_failure_count": self.live_failure_count,
            "last_static_build_ms": self.last_static_build_ms,
            "last_live_build_ms": self.last_live_build_ms,
            "last_error": self.last_error,
            "last_static_error": self.last_static_error,
            "last_live_error": self.last_live_error,
            "static_publication_revision": self.static_publication_revision,
            "live_publication_revision": self.live_publication_revision,
            "static_resource_bytes": len(static["body"]) if static else 0,
            "live_resource_bytes": len(live["body"]) if live else 0,
            "static_resource_id": static["resource_id"] if static else None,
            "live_resource_id": live["resource_id"] if live else None,
            "static_ready": static is not None,
            "live_ready": live is not None,
            "static_summary": deepcopy(self.last_static_summary),
            "live_summary": deepcopy(self.last_live_summary),
            "manifest_reads": self.manifest_reads,
            "manifest_first_read_age_s": self._age_seconds(self._manifest_first_read_mono),
            "manifest_last_read_age_s": self._age_seconds(self._manifest_last_read_mono),
            "static_resource_reads": self.static_resource_reads,
            "static_resource_bytes_served_total": self.static_resource_bytes_served_total,
            "static_resource_not_modified_count": self.static_resource_not_modified_count,
            "static_resource_first_read_age_s": self._age_seconds(self._static_first_read_mono),
            "static_resource_last_read_age_s": self._age_seconds(self._static_last_read_mono),
            "live_resource_reads": self.live_resource_reads,
            "live_resource_bytes_served_total": self.live_resource_bytes_served_total,
            "live_resource_not_modified_count": self.live_resource_not_modified_count,
            "live_resource_first_read_age_s": self._age_seconds(self._live_first_read_mono),
            "live_resource_last_read_age_s": self._age_seconds(self._live_last_read_mono),
            "live_tail_requests": self.live_tail_requests,
            "live_tail_success_count": self.live_tail_success_count,
            "live_tail_full_fallback_count": self.live_tail_full_fallback_count,
            "live_tail_max_points": LIVE_TAIL_MAX_POINTS,
            "live_tail_max_points_observed": self.live_tail_max_points_observed,
            "last_live_tail_point_count": self.last_live_tail_point_count,
            "last_live_tail_base_point_count": self.last_live_tail_base_point_count,
            "last_live_tail_current_point_count": self.last_live_tail_current_point_count,
            "last_live_tail_reason": self.last_live_tail_reason,
            "building": {
                "static": bool(self._static_task and not self._static_task.done()),
                "live_route": bool(self._live_task and not self._live_task.done())
                or self._live_timer is not None,
            },
        }

    async def async_shutdown(self) -> None:
        self._closed = True
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._live_timer is not None:
            self._live_timer.cancel()
            self._live_timer = None
        for task in (self._static_task, self._live_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._static_task, self._live_task):
            if task is not None:
                with suppress(asyncio.CancelledError):
                    await task
        self._static_task = None
        self._live_task = None
        self._static_resources.clear()
        self._live_resources.clear()
