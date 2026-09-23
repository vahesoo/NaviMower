"""Pure Pillow renderer for cached Navimower map snapshots."""
from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import math
import re
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .map_snapshot_mower_art import (
    H2_SNAPSHOT_SVG_PATHS,
    MOWER_ART_HEIGHT,
    MOWER_ART_WIDTH,
)

SNAPSHOT_SIZE = 1024
CUTTING_ACTIONS = {5, 8}

_BACKGROUND = (246, 248, 246, 255)
_ZONE_FILL = (129, 199, 132, 54)
_ZONE_STROKE = (67, 160, 71, 220)
_MOWED = (46, 125, 50, 138)
_ROUTE = (27, 94, 32, 220)
_OFF_LIMIT = (255, 90, 0, 145)
_VF_OFF = (54, 112, 210, 110)
_CHANNEL = (97, 97, 97, 175)
_GATE = (123, 67, 151, 110)
_DOCK = (55, 71, 79, 255)
_TEXT = (55, 71, 79, 255)



_SVG_PATH_TOKEN_RE = re.compile(
    r"[MmLlHhVvCcZz]|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
)


@lru_cache(maxsize=128)
def _svg_path_polygons(path_d: str) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Sample the small SVG command subset used by the embedded mower artwork."""
    tokens = _SVG_PATH_TOKEN_RE.findall(path_d)
    polygons: list[tuple[tuple[float, float], ...]] = []
    current: list[tuple[float, float]] = []
    command: str | None = None
    x = y = 0.0
    start_x = start_y = 0.0
    index = 0

    def number() -> float:
        nonlocal index
        value = float(tokens[index])
        index += 1
        return value

    while index < len(tokens):
        token = tokens[index]
        if token in "MmLlHhVvCcZz":
            command = token
            index += 1
            if command in "Zz":
                if current:
                    if current[-1] != (start_x, start_y):
                        current.append((start_x, start_y))
                    if len(current) >= 3:
                        polygons.append(tuple(current))
                current = []
                x, y = start_x, start_y
                command = None
            continue
        if command is None:
            index += 1
            continue

        relative = command.islower()
        op = command.lower()
        if op == "m":
            nx, ny = number(), number()
            if relative:
                nx += x
                ny += y
            if current and len(current) >= 3:
                polygons.append(tuple(current))
            current = [(nx, ny)]
            x, y = nx, ny
            start_x, start_y = x, y
            command = "l" if relative else "L"
        elif op == "l":
            nx, ny = number(), number()
            if relative:
                nx += x
                ny += y
            x, y = nx, ny
            current.append((x, y))
        elif op == "h":
            nx = number()
            x = x + nx if relative else nx
            current.append((x, y))
        elif op == "v":
            ny = number()
            y = y + ny if relative else ny
            current.append((x, y))
        elif op == "c":
            x1, y1, x2, y2, nx, ny = (
                number(),
                number(),
                number(),
                number(),
                number(),
                number(),
            )
            if relative:
                x1, y1 = x + x1, y + y1
                x2, y2 = x + x2, y + y2
                nx, ny = x + nx, y + ny
            origin_x, origin_y = x, y
            for step in range(1, 7):
                t = step / 6.0
                one = 1.0 - t
                px = (
                    one**3 * origin_x
                    + 3 * one**2 * t * x1
                    + 3 * one * t**2 * x2
                    + t**3 * nx
                )
                py = (
                    one**3 * origin_y
                    + 3 * one**2 * t * y1
                    + 3 * one * t**2 * y2
                    + t**3 * ny
                )
                current.append((px, py))
            x, y = nx, ny
        else:
            # The snapshot artwork intentionally contains only M/L/H/V/C/Z.
            break

    if current and len(current) >= 3:
        polygons.append(tuple(current))
    return tuple(polygons)


def _hex_rgba(value: str) -> tuple[int, int, int, int]:
    text = str(value or "").strip().lstrip("#")
    if len(text) != 6:
        return (61, 67, 78, 255)
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), 255)


@lru_cache(maxsize=1)
def _mower_art_layers() -> tuple[
    tuple[tuple[tuple[float, float], ...], tuple[int, int, int, int]], ...
]:
    layers = []
    for translate_x, translate_y, path_d, fill in H2_SNAPSHOT_SVG_PATHS:
        for polygon in _svg_path_polygons(path_d):
            layers.append(
                (
                    tuple(
                        (point[0] + translate_x, point[1] + translate_y)
                        for point in polygon
                    ),
                    _hex_rgba(fill),
                )
            )
    return tuple(layers)


def _as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _xy_points(value: Any) -> list[list[float]]:
    if isinstance(value, dict):
        for key in ("polygon", "points", "path"):
            if key in value:
                return _xy_points(value.get(key))
        return []
    if not isinstance(value, (list, tuple)):
        return []
    result: list[list[float]] = []
    for raw in value:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        x = _as_float(raw[0])
        y = _as_float(raw[1])
        if x is None or y is None:
            continue
        result.append([x, y])
    return result


def _session_cutting_segments(session: Any) -> list[list[list[float]]]:
    """Mirror the session archive's conservative blade-on edge classification."""
    if not isinstance(session, dict):
        return []
    starts = {
        stamp
        for raw in session.get("segment_starts_ms") or []
        if (stamp := _as_int(raw)) is not None
    }
    points: list[tuple[int, float, float, bool, int | None]] = []
    for raw in session.get("points") or []:
        if not isinstance(raw, list) or len(raw) < 3:
            continue
        stamp = _as_int(raw[0])
        x = _as_float(raw[1])
        y = _as_float(raw[2])
        if stamp is None or x is None or y is None:
            continue
        action = _as_int(raw[6]) if len(raw) > 6 else None
        activity = str(raw[4] if len(raw) > 4 else "").strip().lower()
        if action is not None:
            cutting = action in CUTTING_ACTIONS
        else:
            cutting = (
                activity not in {"docked", "paused", "returning", "error"}
                and (activity == "mowing" or "mow" in activity or "cut" in activity)
            )
        zone_id = _as_int(raw[7]) if len(raw) > 7 else None
        points.append((stamp, x, y, cutting, zone_id))

    fragments: list[list[tuple[int, float, float, bool, int | None]]] = []
    current: list[tuple[int, float, float, bool, int | None]] = []
    for point in points:
        if current and point[0] in starts:
            fragments.append(current)
            current = []
        current.append(point)
    if current:
        fragments.append(current)

    result: list[list[list[float]]] = []
    for fragment in fragments:
        segment: list[list[float]] = []
        for previous, current_point in zip(fragment, fragment[1:]):
            edge_cutting = (
                previous[3]
                and current_point[3]
                and (previous[4] is not None or current_point[4] is not None)
            )
            start_xy = [previous[1], previous[2]]
            end_xy = [current_point[1], current_point[2]]
            if not edge_cutting or start_xy == end_xy:
                if len(segment) >= 2:
                    result.append(segment)
                segment = []
                continue
            if not segment:
                segment = [start_xy, end_xy]
            else:
                if segment[-1] != start_xy:
                    segment.append(start_xy)
                segment.append(end_xy)
        if len(segment) >= 2:
            result.append(segment)
    return result


def _static_points(source: dict[str, Any]) -> list[list[float]]:
    map_data = source.get("map") or {}
    result: list[list[float]] = []
    for zone in map_data.get("zones") or []:
        result.extend(_xy_points(zone))
    for key in ("off_limit_areas", "vf_off_areas"):
        for area in map_data.get(key) or []:
            result.extend(_xy_points(area))
    for gate in source.get("gate_areas") or []:
        result.extend(_xy_points(gate))
    station = map_data.get("station") or {}
    sx = _as_float(station.get("x")) if isinstance(station, dict) else None
    sy = _as_float(station.get("y")) if isinstance(station, dict) else None
    if sx is not None and sy is not None:
        result.append([sx, sy])
    return result


def _projector(points: list[list[float]], size: int):
    if not points:
        points = [[-5.0, -5.0], [5.0, 5.0]]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 0.5)
    span_y = max(max_y - min_y, 0.5)
    pad = max(span_x, span_y) * 0.08 + 0.5
    min_x -= pad
    max_x += pad
    min_y -= pad
    max_y += pad
    span_x = max_x - min_x
    span_y = max_y - min_y
    scale = min((size - 1) / span_x, (size - 1) / span_y)
    draw_w = span_x * scale
    draw_h = span_y * scale
    offset_x = (size - draw_w) / 2.0
    offset_y = (size - draw_h) / 2.0

    def project(point: list[float] | tuple[float, float]) -> tuple[float, float]:
        return (
            offset_x + (float(point[0]) - min_x) * scale,
            offset_y + (max_y - float(point[1])) * scale,
        )

    return project, scale


def _draw_round_line(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    *,
    fill: tuple[int, int, int, int],
    width: int,
) -> None:
    if len(points) < 2:
        return
    draw.line(points, fill=fill, width=max(1, width), joint="curve")
    radius = max(1, width) / 2.0
    for x, y in (points[0], points[-1]):
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=fill,
        )


def _draw_polygon_layer(
    image: Image.Image,
    values: Any,
    project,
    *,
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int, int],
    outline_width: int = 2,
) -> None:
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for value in values or []:
        points = _xy_points(value)
        if len(points) < 3:
            continue
        projected = [project(point) for point in points]
        draw.polygon(projected, fill=fill)
        draw.line(projected + [projected[0]], fill=outline, width=outline_width, joint="curve")
    image.alpha_composite(layer)


def _draw_zone_labels(
    image: Image.Image,
    zones: Any,
    project,
    *,
    size: int,
) -> None:
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=max(12, round(size / 64)))
    except TypeError:
        font = ImageFont.load_default()
    for zone in zones or []:
        if not isinstance(zone, dict):
            continue
        points = _xy_points(zone)
        name = str(zone.get("name") or "").strip()
        if len(points) < 3 or not name:
            continue
        cx = sum(point[0] for point in points) / len(points)
        cy = sum(point[1] for point in points) / len(points)
        x, y = project([cx, cy])
        bbox = draw.textbbox((0, 0), name, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        pad_x, pad_y = 5, 3
        draw.rounded_rectangle(
            (x - w / 2 - pad_x, y - h / 2 - pad_y, x + w / 2 + pad_x, y + h / 2 + pad_y),
            radius=6,
            fill=(245, 247, 248, 225),
            outline=(176, 190, 197, 220),
            width=1,
        )
        draw.text((x - w / 2, y - h / 2 - bbox[1]), name, font=font, fill=_TEXT)


def _draw_station(image: Image.Image, station: Any, project, scale: float) -> None:
    if not isinstance(station, dict):
        return
    x = _as_float(station.get("x"))
    y = _as_float(station.get("y"))
    if x is None or y is None:
        return
    px, py = project([x, y])
    radius = max(7.0, min(15.0, scale * 0.35))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (px - radius, py - radius * 0.8, px + radius, py + radius * 0.8),
        radius=max(3, radius * 0.25),
        fill=_DOCK,
        outline=(255, 255, 255, 255),
        width=2,
    )
    draw.line(
        [(px + 1, py - radius * 0.45), (px - 3, py), (px + 1, py), (px - 1, py + radius * 0.45)],
        fill=(105, 240, 174, 255),
        width=max(2, round(radius / 4)),
    )


def _draw_mower(image: Image.Image, position: Any, project, scale: float) -> None:
    """Draw the Map Card mower SVG artwork at the live local-map pose."""
    if not isinstance(position, dict):
        return
    x = _as_float(position.get("x"))
    y = _as_float(position.get("y"))
    if x is None or y is None:
        return
    heading = _as_float(position.get("heading"))
    if heading is None:
        heading = 0.0

    px, py = project([x, y])
    # Keep notification snapshots legible on both very large and small maps.
    target_height = max(34.0, min(64.0, image.width / 18.0))
    artwork_scale = target_height / MOWER_ART_HEIGHT

    # The Map Card artwork faces SVG-up. Coordinator heading is raw
    # postureTheta radians, where heading=0 points along local +X. Replicate the
    # card's screen transform: rotate(90deg - heading).
    angle = math.pi / 2.0 - heading
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    center_x = MOWER_ART_WIDTH / 2.0
    center_y = MOWER_ART_HEIGHT / 2.0

    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for layer_index, (polygon, fill) in enumerate(_mower_art_layers()):
        transformed = []
        for art_x, art_y in polygon:
            dx = (art_x - center_x) * artwork_scale
            dy = (art_y - center_y) * artwork_scale
            transformed.append(
                (
                    px + dx * cos_a - dy * sin_a,
                    py + dx * sin_a + dy * cos_a,
                )
            )
        if len(transformed) < 3:
            continue
        draw.polygon(
            transformed,
            fill=fill,
            outline=(255, 255, 255, 235) if layer_index == 0 else None,
            width=max(1, round(image.width / 512)) if layer_index == 0 else 1,
        )
    image.alpha_composite(layer)


def _draw_placeholder(image: Image.Image, text: str) -> None:
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=20)
    except TypeError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (image.width - (bbox[2] - bbox[0])) / 2
    y = (image.height - (bbox[3] - bbox[1])) / 2
    draw.text((x, y), text, font=font, fill=(100, 110, 105, 255))


def render_snapshot_png(source: dict[str, Any], size: int = SNAPSHOT_SIZE) -> bytes:
    """Render one backend-owned latest-map snapshot as PNG bytes."""
    size = max(320, min(1600, int(size)))
    image = Image.new("RGBA", (size, size), _BACKGROUND)
    map_data = source.get("map") or {}
    static = _static_points(source)
    position = source.get("position") or {}
    if not static:
        px = _as_float(position.get("x")) if isinstance(position, dict) else None
        py = _as_float(position.get("y")) if isinstance(position, dict) else None
        if px is not None and py is not None:
            static = [[px - 5.0, py - 5.0], [px + 5.0, py + 5.0]]
    project, scale = _projector(static, size)

    zones = map_data.get("zones") or []
    _draw_polygon_layer(
        image,
        zones,
        project,
        fill=_ZONE_FILL,
        outline=_ZONE_STROKE,
        outline_width=max(1, round(size / 512)),
    )
    _draw_polygon_layer(
        image,
        map_data.get("off_limit_areas") or [],
        project,
        fill=_OFF_LIMIT,
        outline=(216, 67, 21, 220),
    )
    _draw_polygon_layer(
        image,
        map_data.get("vf_off_areas") or [],
        project,
        fill=_VF_OFF,
        outline=(41, 98, 180, 220),
    )
    _draw_polygon_layer(
        image,
        source.get("gate_areas") or [],
        project,
        fill=_GATE,
        outline=(94, 53, 177, 230),
    )

    channel_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    channel_draw = ImageDraw.Draw(channel_layer)
    for channel in map_data.get("channels") or []:
        points = _xy_points(channel)
        if len(points) < 2:
            continue
        projected = [project(point) for point in points]
        _draw_round_line(
            channel_draw,
            projected,
            fill=_CHANNEL,
            width=max(2, round(size / 256)),
        )
    image.alpha_composite(channel_layer)

    mowed_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    mowed_draw = ImageDraw.Draw(mowed_layer)
    width_m = _as_float(source.get("mowing_path_width_m"))
    if width_m is None or not 0.1 <= width_m <= 2.0:
        width_m = 0.25
    stroke_px = max(3, round(width_m * scale))

    mowed_segments: list[list[list[float]]] = []
    for segment in source.get("vendor_segments") or []:
        clean = _xy_points(segment)
        if len(clean) >= 2:
            mowed_segments.append(clean)
    mowed_segments.extend(_session_cutting_segments(source.get("fallback_session")))

    for segment in mowed_segments:
        _draw_round_line(
            mowed_draw,
            [project(point) for point in segment],
            fill=_MOWED,
            width=stroke_px,
        )
    image.alpha_composite(mowed_layer)

    route_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    route_draw = ImageDraw.Draw(route_layer)
    for segment in source.get("live_route_segments") or []:
        clean = _xy_points(segment)
        if len(clean) < 2:
            continue
        _draw_round_line(
            route_draw,
            [project(point) for point in clean],
            fill=_ROUTE,
            width=max(2, round(size / 384)),
        )
    image.alpha_composite(route_layer)

    _draw_station(image, map_data.get("station"), project, scale)
    _draw_mower(image, position, project, scale)
    if source.get("show_zone_labels", True):
        _draw_zone_labels(image, zones, project, size=size)

    if not zones and not static:
        _draw_placeholder(image, "Navimower map unavailable")

    output = BytesIO()
    image.convert("RGB").save(output, format="PNG", compress_level=6)
    return output.getvalue()
