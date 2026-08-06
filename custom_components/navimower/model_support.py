"""Small model-family compatibility helpers."""
from __future__ import annotations

from typing import Final

# First-generation Navimow H-series models do support selecting one or more
# mowing zones, but do not expose the later app feature that lets the user set a
# custom zone sequence. Keep this list explicit so newer H-prefixed models such
# as H215 are never classified as legacy H1 by a broad prefix check.
H1_GENERATION_MODELS: Final[frozenset[str]] = frozenset(
    {
        "H500",
        "H500E",
        "H800",
        "H800E",
        "H1500",
        "H1500E",
        "H3000",
        "H3000E",
    }
)

# Observed on the H1500 diagnostics. Retain this as a fallback for entries whose
# model name is missing or formatted differently by the account endpoint.
H1_GENERATION_VEHICLE_TYPES: Final[frozenset[int]] = frozenset({20000002})


def is_h1_generation(model: str | None, vehicle_type: int | None = None) -> bool:
    """Return whether this mower belongs to the first-generation H series."""
    normalized = str(model or "").strip().upper().replace(" ", "")
    try:
        parsed_vehicle_type = int(vehicle_type or 0)
    except (TypeError, ValueError):
        parsed_vehicle_type = 0
    return (
        normalized in H1_GENERATION_MODELS
        or parsed_vehicle_type in H1_GENERATION_VEHICLE_TYPES
    )


def supports_ordered_zone_mowing(
    model: str | None, vehicle_type: int | None = None
) -> bool:
    """Return whether the mower accepts a user-defined zone sequence."""
    return not is_h1_generation(model, vehicle_type)
