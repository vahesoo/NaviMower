"""Shared map identifier resolution for Navimower cloud endpoints."""
from __future__ import annotations

from typing import Any


_INVALID_MAP_IDENTIFIERS = {"", "0"}


def valid_map_identifier(value: Any) -> bool:
    """Return whether a cloud map identifier can be used for map requests."""
    if value is None:
        return False
    return str(value).strip() not in _INVALID_MAP_IDENTIFIERS


def resolve_map_identifiers(
    location: Any,
    map_list: Any,
) -> tuple[str | None, str | None, str | None]:
    """Resolve the first usable map id, base id and edit time.

    Older H1 mowers can report ``0`` identifiers from the location endpoint
    while docked even though ``map-list`` contains the valid map. Treat zero and
    empty values as missing and continue through all map-list candidates.
    """
    candidates: list[dict[str, Any]] = []
    if isinstance(location, dict):
        candidates.append(location)

    if isinstance(map_list, list):
        candidates.extend(item for item in map_list if isinstance(item, dict))
    elif isinstance(map_list, dict):
        candidates.append(map_list)
        for key in ("list", "maps", "data"):
            rows = map_list.get(key)
            if isinstance(rows, list):
                candidates.extend(item for item in rows if isinstance(item, dict))

    for item in candidates:
        map_id = item.get("map_id", item.get("mapId"))
        map_base_id = item.get("map_base_id", item.get("mapBaseId"))
        if not (
            valid_map_identifier(map_id)
            and valid_map_identifier(map_base_id)
        ):
            continue
        edit_time = item.get(
            "map_edit_time",
            item.get("edittime", item.get("editTime")),
        )
        return str(map_id), str(map_base_id), str(edit_time or "")

    return None, None, None
