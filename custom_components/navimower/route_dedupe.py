"""Pure helpers for coalescing duplicate Navimower route samples.

A vendor pose is identified by timestamp, X/Y, heading and activity. MQTT
state/action and resolved zone are observation metadata and may legitimately be
missing from one delivery and present in another. Metadata-only differences
must enrich the retained point instead of creating duplicate trail geometry.
"""
from __future__ import annotations

from typing import Any

_POSE_IDENTITY_FIELDS = 5
_METADATA_FIELDS = (5, 6, 7)


def _same_pose_identity(left: list[Any], right: list[Any]) -> bool:
    """Return whether two detail points describe the same vendor pose/state."""
    return (
        len(left) >= _POSE_IDENTITY_FIELDS
        and len(right) >= _POSE_IDENTITY_FIELDS
        and left[:_POSE_IDENTITY_FIELDS] == right[:_POSE_IDENTITY_FIELDS]
    )


def _enrich_metadata(retained: list[Any], incoming: list[Any]) -> None:
    """Fill missing route metadata without replacing known observations."""
    target_len = max(len(retained), len(incoming), max(_METADATA_FIELDS) + 1)
    if len(retained) < target_len:
        retained.extend([None] * (target_len - len(retained)))
    for index in _METADATA_FIELDS:
        value = incoming[index] if index < len(incoming) else None
        if retained[index] is None and value is not None:
            retained[index] = value


def append_or_coalesce(points: list[list[Any]], sample: list[Any]) -> bool:
    """Append a new route sample, or enrich the identical latest vendor pose.

    Returns True only when route geometry gained a point. A metadata-only
    enrichment returns False so map trail revision counters do not churn.
    """
    if points and isinstance(points[-1], list) and _same_pose_identity(points[-1], sample):
        _enrich_metadata(points[-1], sample)
        return False
    points.append(list(sample))
    return True


def compact_route_points(points: list[Any] | None) -> list[list[Any]]:
    """Coalesce persisted duplicate pose identities while preserving order.

    This also repairs beta15/beta16 history where the same private-cloud pose was
    retained repeatedly as MQTT context alternated between values such as 4/5
    and None/None. Different activities remain separate points even at the same
    timestamp/location so real mowing/error transitions are not erased.
    """
    compacted: list[list[Any]] = []
    by_identity: dict[tuple[Any, ...], list[Any]] = {}
    for raw in points or []:
        if not isinstance(raw, list):
            continue
        point = list(raw)
        if len(point) < _POSE_IDENTITY_FIELDS:
            compacted.append(point)
            continue
        identity = tuple(point[:_POSE_IDENTITY_FIELDS])
        retained = by_identity.get(identity)
        if retained is not None:
            _enrich_metadata(retained, point)
            continue
        compacted.append(point)
        by_identity[identity] = point
    return compacted
