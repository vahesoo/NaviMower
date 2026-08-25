"""Regression coverage for the Map Card frontend metadata fast path."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAP_API = ROOT / "custom_components" / "navimower" / "map_api.py"


def test_map_api_exposes_frontend_entity_metadata() -> None:
    source = MAP_API.read_text(encoding="utf-8")
    assert "device_registry as dr, entity_registry as er" in source
    assert "def _frontend_metadata(coordinator" in source
    assert '"frontend": _frontend_metadata(coordinator)' in source
    assert '"device_id": device.id if device is not None else None' in source
    for key in (
        '"mower"',
        '"map_data"',
        '"position_x"',
        '"position_y"',
        '"heading"',
        '"battery"',
        '"current_physical_zone"',
        '"schedule_status"',
        '"managed_schedule"',
        '"native_schedule"',
        '"schedule_start"',
        '"schedule_end"',
    ):
        assert key in source
    assert "async_get_entity_id" in source
    assert "entity_registry.async_get_entity_id" in source


def test_lightweight_and_full_map_payloads_share_frontend_metadata() -> None:
    source = MAP_API.read_text(encoding="utf-8")
    assert "return _with_card_metadata(coordinator, await coordinator.async_map_payload())" in source
    assert "return _with_card_metadata(coordinator, payload)" in source
