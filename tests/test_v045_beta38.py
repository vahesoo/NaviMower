"""Regression contracts for Navimower 0.4.5-beta38 LiDAR capability."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta38_release_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.5-beta38"
    notes = (ROOT / ".github" / "release-notes" / "0.4.5-beta38.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "resource-driven",
        "type=2",
        "H2",
        "future models",
        "terrain_overlay.supported",
    ):
        assert phrase in notes


def test_beta38_resource_is_frontend_capability_authority() -> None:
    terrain = (COMPONENT / "terrain_overlay.py").read_text(encoding="utf-8")

    assert "def model_hint(self) -> bool:" in terrain
    assert "def supported(self) -> bool:" in terrain
    assert 'manifest.get("images")' in terrain
    assert "capability_profile(model, vehicle_type).lidar_terrain_overlay" in terrain
    assert "if self._manifest is not None or self.model_hint" in terrain

    # Model knowledge may accelerate polling, but must not prevent cloud/resource discovery.
    assert 'if not self.supported:\n            return' not in terrain
    assert 'if not self.model_hint:\n            return' not in terrain


def test_beta38_diagnostics_explain_capability_source() -> None:
    terrain = (COMPONENT / "terrain_overlay.py").read_text(encoding="utf-8")
    assert '"support_source": "vendor_type2_resource" if self.supported else None' in terrain
    assert '"model_hint": self.model_hint' in terrain
