"""Normalize Navimow vendor hint/error catalog data for diagnostics and lookup."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

_CATALOG_SECTIONS = (
    "vehicleErrorData",
    "vehicleEventData",
    "mapErrorData",
    "warningData",
)
_MAX_LOOKUP_ENTRIES = 1024


def normalize_vendor_code(value: Any) -> str | None:
    """Return a stable uppercase vendor code without guessing its meaning."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text or len(text) > 32:
        return None
    return text


def _clean_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _entry(section: str, raw: Mapping[str, Any]) -> dict[str, Any] | None:
    code = normalize_vendor_code(raw.get("error_code"))
    if not code:
        return None
    return {
        "code": code,
        "section": section,
        "title": _clean_text(raw.get("title"), limit=240),
        "content": _clean_text(raw.get("content"), limit=1000),
        "level": _clean_text(raw.get("level"), limit=32),
        "name": _clean_text(raw.get("name"), limit=120),
        "relate_id": _clean_text(raw.get("relate_id"), limit=32),
        "updatetime": raw.get("updatetime"),
    }


def build_error_catalog(inspection: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build a bounded code lookup from a decoded get-hint-error-compress result."""
    decoded = inspection.get("decoded") if isinstance(inspection, Mapping) else None
    data = decoded.get("decoded_data") if isinstance(decoded, Mapping) else None
    if not isinstance(data, Mapping):
        return {
            "available": False,
            "code_count": 0,
            "section_counts": {},
            "lookup": {},
        }

    lookup: dict[str, list[dict[str, Any]]] = {}
    section_counts: dict[str, int] = {}
    dropped = 0
    for section in _CATALOG_SECTIONS:
        rows = data.get(section)
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
            continue
        count = 0
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            normalized = _entry(section, raw)
            if normalized is None:
                continue
            count += 1
            code = normalized["code"]
            if code not in lookup and len(lookup) >= _MAX_LOOKUP_ENTRIES:
                dropped += 1
                continue
            entries = lookup.setdefault(code, [])
            if normalized not in entries:
                entries.append(normalized)
        section_counts[section] = count

    return {
        "available": bool(lookup),
        "code_count": len(lookup),
        "section_counts": section_counts,
        "dropped_codes": dropped,
        "vehicle_update_time": data.get("vehicle_update_time"),
        "map_update_time": data.get("map_update_time"),
        "lookup": lookup,
    }


def resolve_error_code(catalog: Mapping[str, Any] | None, code: Any) -> list[dict[str, Any]]:
    """Resolve one exact vendor code without fuzzy matching or state inference."""
    normalized = normalize_vendor_code(code)
    if not normalized or not isinstance(catalog, Mapping):
        return []
    lookup = catalog.get("lookup")
    if not isinstance(lookup, Mapping):
        return []
    matches = lookup.get(normalized)
    if not isinstance(matches, list):
        return []
    return deepcopy(matches)
