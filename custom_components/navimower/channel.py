"""Local channel/corridor geometry used by gate automations and the map card."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Any


@dataclass(frozen=True, slots=True)
class NavimowerChannel:
    """A rectangular local-coordinate channel."""

    name: str
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    @property
    def slug(self) -> str:
        value = re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_")
        return value or "channel"

    def contains(self, x: Any, y: Any) -> bool | None:
        """Return whether a valid pose is inside this rectangle."""
        try:
            px, py = float(x), float(y)
        except (TypeError, ValueError):
            return None
        return self.x_min <= px <= self.x_max and self.y_min <= py <= self.y_max

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "slug": self.slug}


def parse_channels(raw: Any) -> list[NavimowerChannel]:
    """Parse an options JSON list into validated channel rectangles.

    Example::

        [{"name":"Gate","x_min":1,"x_max":3,"y_min":-2,"y_max":2}]
    """
    if raw in (None, "", []):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []

    channels: list[NavimowerChannel] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            name = str(item.get("name") or "Channel").strip()
            x1, x2 = float(item["x_min"]), float(item["x_max"])
            y1, y2 = float(item["y_min"]), float(item["y_max"])
        except (KeyError, TypeError, ValueError):
            continue
        channel = NavimowerChannel(
            name=name or "Channel",
            x_min=min(x1, x2),
            x_max=max(x1, x2),
            y_min=min(y1, y2),
            y_max=max(y1, y2),
        )
        slug = channel.slug
        if slug in seen:
            continue
        seen.add(slug)
        channels.append(channel)
    return channels
