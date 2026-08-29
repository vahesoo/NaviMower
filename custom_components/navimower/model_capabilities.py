"""Conservative model-family capabilities backed by manuals and field evidence.

Vendor ``set-list`` uses a broad shared schema and therefore cannot by itself prove
that a setting is user-facing on a particular mower.  This module records the
small set of family differences that we can currently defend from official model
behavior plus Navimower field tests.  Device-reported option/range metadata still
wins whenever it is available.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .model_support import is_h1_generation

FAMILY_H1: Final = "h1"
FAMILY_H2: Final = "h2"
FAMILY_I1: Final = "i1"
FAMILY_I2_AWD: Final = "i2_awd"
FAMILY_I2_LIDAR: Final = "i2_lidar"
FAMILY_I2_UNKNOWN: Final = "i2_unknown"
FAMILY_X3: Final = "x3"
FAMILY_X4: Final = "x4"
FAMILY_TERRANOX: Final = "terranox"
FAMILY_UNKNOWN: Final = "unknown"


@dataclass(frozen=True)
class ModelCapabilityProfile:
    """Known family behavior; ``False`` means do not expose remote control yet."""

    family: str
    cutting_height_adjustment: str | None = None
    cutting_height_range_mm: tuple[int, int] | None = None
    cutting_height_readable: bool = False
    cutting_height_writable: bool = False
    traction_control: bool = False
    terrain_adapt: bool = False
    edge_sense: bool = False
    grass_pattern_enhancement: bool = False


_PROFILES: Final[dict[str, ModelCapabilityProfile]] = {
    FAMILY_H1: ModelCapabilityProfile(
        family=FAMILY_H1,
        cutting_height_adjustment="electronic",
        cutting_height_range_mm=(30, 60),
        cutting_height_readable=True,
        cutting_height_writable=True,
    ),
    FAMILY_H2: ModelCapabilityProfile(
        family=FAMILY_H2,
        cutting_height_adjustment="electronic",
        cutting_height_range_mm=(20, 70),
        cutting_height_readable=True,
        cutting_height_writable=True,
        terrain_adapt=True,
        edge_sense=True,
    ),
    # i1 exposes the physical 20-60 mm range in device metadata, but the user
    # adjusts the deck with the mower's manual knob.  Until a knob-position
    # sensor is proven by before/after diagnostics, do not treat set-list.height
    # as the current deck height and never expose a remote height writer.
    FAMILY_I1: ModelCapabilityProfile(
        family=FAMILY_I1,
        cutting_height_adjustment="manual",
        cutting_height_range_mm=(20, 60),
    ),
    FAMILY_I2_AWD: ModelCapabilityProfile(
        family=FAMILY_I2_AWD,
        cutting_height_adjustment="electronic",
        cutting_height_range_mm=(20, 60),
        cutting_height_readable=True,
        cutting_height_writable=True,
        traction_control=True,
    ),
    FAMILY_I2_LIDAR: ModelCapabilityProfile(
        family=FAMILY_I2_LIDAR,
        cutting_height_adjustment="electronic",
        cutting_height_range_mm=(20, 70),
        cutting_height_readable=True,
        cutting_height_writable=True,
        edge_sense=True,
    ),
    FAMILY_I2_UNKNOWN: ModelCapabilityProfile(family=FAMILY_I2_UNKNOWN),
    FAMILY_X3: ModelCapabilityProfile(
        family=FAMILY_X3,
        cutting_height_adjustment="electronic",
        cutting_height_range_mm=(20, 70),
        cutting_height_readable=True,
        cutting_height_writable=True,
    ),
    FAMILY_X4: ModelCapabilityProfile(
        family=FAMILY_X4,
        cutting_height_adjustment="electronic",
        cutting_height_range_mm=(20, 95),
        cutting_height_readable=True,
        cutting_height_writable=True,
        traction_control=True,
        grass_pattern_enhancement=True,
    ),
    # Terranox and X4 share the enhancement concept, but we do not have a
    # Terranox private-cloud diagnostic/write capture yet.  Keep physical
    # capabilities documented while remote height writes remain disabled.
    FAMILY_TERRANOX: ModelCapabilityProfile(
        family=FAMILY_TERRANOX,
        cutting_height_adjustment="electronic",
        cutting_height_range_mm=(19, 102),
        traction_control=True,
        grass_pattern_enhancement=True,
    ),
    FAMILY_UNKNOWN: ModelCapabilityProfile(family=FAMILY_UNKNOWN),
}


def _normalized_model(model: str | None) -> str:
    return "".join(ch for ch in str(model or "").upper() if ch.isalnum())


def model_family(model: str | None, vehicle_type: int | None = None) -> str:
    """Resolve the known family without broad H-prefix misclassification."""
    if is_h1_generation(model, vehicle_type):
        return FAMILY_H1
    normalized = _normalized_model(model)
    if normalized.startswith("H2"):
        return FAMILY_H2
    if normalized.startswith("I1"):
        return FAMILY_I1
    if normalized.startswith("I2"):
        if "AWD" in normalized:
            return FAMILY_I2_AWD
        if "LIDAR" in normalized:
            return FAMILY_I2_LIDAR
        return FAMILY_I2_UNKNOWN
    if normalized.startswith("X3"):
        return FAMILY_X3
    if normalized.startswith("X4"):
        return FAMILY_X4
    if normalized.startswith("CM") or "TERRANOX" in normalized:
        return FAMILY_TERRANOX
    return FAMILY_UNKNOWN


def capability_profile(
    model: str | None, vehicle_type: int | None = None
) -> ModelCapabilityProfile:
    """Return the conservative family profile for one mower."""
    return _PROFILES[model_family(model, vehicle_type)]


__all__ = [
    "FAMILY_H1",
    "FAMILY_H2",
    "FAMILY_I1",
    "FAMILY_I2_AWD",
    "FAMILY_I2_LIDAR",
    "FAMILY_I2_UNKNOWN",
    "FAMILY_TERRANOX",
    "FAMILY_UNKNOWN",
    "FAMILY_X3",
    "FAMILY_X4",
    "ModelCapabilityProfile",
    "capability_profile",
    "model_family",
]
