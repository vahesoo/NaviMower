"""Regression contracts for Navimower 0.4.5-beta32."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta32_version_floor() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    assert version.startswith("0.4.5-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 32


def test_beta32_charge_limit_uses_positive_capability_evidence() -> None:
    models = (COMPONENT / "model_capabilities.py").read_text(encoding="utf-8")
    numbers = (COMPONENT / "number.py").read_text(encoding="utf-8")
    semantics = (COMPONENT / "capability_semantics.py").read_text(encoding="utf-8")

    assert 'FAMILY_H5: Final = "h5"' in models
    assert 'normalized.startswith("H5")' in models
    assert "charging_limit_control: bool | None = None" in models

    h2 = models[models.index("FAMILY_H2: ModelCapabilityProfile("):models.index("FAMILY_I1: ModelCapabilityProfile(")]
    i1 = models[models.index("FAMILY_I1: ModelCapabilityProfile("):models.index("FAMILY_I2_AWD: ModelCapabilityProfile(")]
    x3 = models[models.index("FAMILY_X3: ModelCapabilityProfile("):models.index("FAMILY_H5: ModelCapabilityProfile(")]
    h5 = models[models.index("FAMILY_H5: ModelCapabilityProfile("):models.index("FAMILY_X4: ModelCapabilityProfile(")]

    assert "charging_limit_control=True" in h2
    assert "charging_limit_control=True" in i1
    assert "charging_limit_control=False" in x3
    assert "charging_limit_control=True" in h5

    assert "def _charging_limit_supported(" in numbers
    assert "profile.charging_limit_control is not None" in numbers
    assert 'config.get("chargingLimitMin")' in numbers
    assert 'config.get("chargingLimitMax")' in numbers
    assert 'desc.key == "charging_limit"' in numbers
    assert "_description_supported(desc, data)" in numbers

    assert '"charging_limit_writable"' in semantics
    assert '"family_field_or_app_evidence"' in semantics
    assert '"family_app_absence"' in semantics
    assert '"unproven_shared_schema_field"' in semantics


def test_beta32_snapshot_uses_svg_derived_mower_artwork() -> None:
    render = (COMPONENT / "map_snapshot_render.py").read_text(encoding="utf-8")
    assert "H2_SNAPSHOT_SVG_PATHS" in render
    assert "Map Card H2 SVG" in render
    assert "def _svg_path_polygons(" in render
    assert "def _mower_art_layers(" in render
    assert "rotate(90deg - heading)" in render
    assert "_MOWER_FRONT" not in render
