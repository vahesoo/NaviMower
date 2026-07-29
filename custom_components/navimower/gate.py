"""Bidirectional zone-pair gates used by mower transition automations."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Any


@dataclass(frozen=True, slots=True)
class NavimowerGate:
    """A physical gate connecting two mapped mower zones."""

    name: str
    zone_a: int
    zone_b: int

    @property
    def slug(self) -> str:
        value = re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_")
        return value or f"zone_{self.zone_a}_zone_{self.zone_b}"

    @property
    def zones(self) -> tuple[int, int]:
        return (self.zone_a, self.zone_b)

    def contains_zone(self, zone_id: Any) -> bool:
        try:
            value = int(zone_id)
        except (TypeError, ValueError):
            return False
        return value in self.zones

    def other_zone(self, zone_id: Any) -> int | None:
        try:
            value = int(zone_id)
        except (TypeError, ValueError):
            return None
        if value == self.zone_a:
            return self.zone_b
        if value == self.zone_b:
            return self.zone_a
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "zones": [self.zone_a, self.zone_b],
            "slug": self.slug,
        }


def parse_gates(raw: Any) -> list[NavimowerGate]:
    """Parse a JSON list of bidirectional zone-pair gate definitions.

    Preferred format::

        [{"name": "Back yard gate", "zones": [13, 24]}]

    ``zone_a``/``zone_b`` and ``from_zone``/``to_zone`` are accepted as
    compatibility aliases. Zone order does not matter; each gate is always
    bidirectional.
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

    gates: list[NavimowerGate] = []
    seen_slugs: set[str] = set()
    seen_pairs: set[frozenset[int]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        zones = item.get("zones")
        if isinstance(zones, (list, tuple)) and len(zones) == 2:
            first, second = zones
        else:
            first = item.get("zone_a", item.get("from_zone"))
            second = item.get("zone_b", item.get("to_zone"))
        try:
            zone_a = int(first)
            zone_b = int(second)
        except (TypeError, ValueError):
            continue
        if zone_a <= 0 or zone_b <= 0 or zone_a == zone_b:
            continue
        pair = frozenset((zone_a, zone_b))
        if pair in seen_pairs:
            continue
        name = str(item.get("name") or f"Zone {zone_a} - Zone {zone_b} gate").strip()
        gate = NavimowerGate(name=name or "Gate", zone_a=zone_a, zone_b=zone_b)
        if gate.slug in seen_slugs:
            continue
        seen_pairs.add(pair)
        seen_slugs.add(gate.slug)
        gates.append(gate)
    return gates


def valid_gates_config(raw: Any) -> bool:
    """Return whether every JSON item produces one unique valid gate."""
    if raw in (None, "", []):
        return True
    parsed_raw = raw
    if isinstance(parsed_raw, str):
        try:
            parsed_raw = json.loads(parsed_raw)
        except (TypeError, ValueError):
            return False
    if not isinstance(parsed_raw, list) or not parsed_raw:
        return False
    return len(parse_gates(parsed_raw)) == len(parsed_raw)
