"""Regression coverage for Map Card mower artwork metadata."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MOWER = ROOT / "custom_components" / "navimower" / "lawn_mower.py"


def test_mower_entity_exposes_model_without_device_registry_scan() -> None:
    source = MOWER.read_text(encoding="utf-8")
    assert '"model": data.get("model") or self.coordinator.entry.data.get("model")' in source
    assert '"vehicle_type": self.coordinator.vehicle_type' in source
    assert '"map_api_path": f"/api/navimower/map/{self.coordinator.entry.entry_id}"' in source
