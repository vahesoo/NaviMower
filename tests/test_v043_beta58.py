"""Dependency-free regression coverage for 0.4.3-beta58 behavior."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _source(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


def test_beta58_single_mower_setup_skips_redundant_picker() -> None:
    source = _source("setup_flow_semantics.py")
    assert 'result.get("step_id") == "select_vehicle"' in source
    assert "len(self._vehicles) == 1" in source
    assert "self._prepare_vehicle(self._vehicles[0])" in source
    assert "install_setup_flow_semantics" in _source("runtime.py")


def test_beta58_schedule_reset_button_uses_existing_controller_contract() -> None:
    init = _source("__init__.py")
    button = _source("button.py")
    assert "Platform.BUTTON" in init
    assert 'super().__init__(coordinator, "navimower_schedule_reset")' in button
    assert '_attr_name = "Reset schedule progress"' in button
    assert 'reason="home_assistant_button"' in button
    assert "async_reset_schedule" in button
    assert "_vendor_mowing" in button
    assert "ACTIVITY_RETURNING" in button


def test_beta58_stale_zone_cleanup_requires_authoritative_map() -> None:
    source = _source("zone_entity_cleanup.py")
    assert 'map_data.get("revision") or map_data.get("map_version")' in source
    assert "not isinstance(zones, list) or not zones" in source
    assert "er.async_entries_for_config_entry" in source
    assert "registry.async_remove" in source
    assert 'registry_entry.domain != "sensor"' in source
    assert "zone_id in current_zone_ids" in source
    assert "install_zone_entity_cleanup" in _source("runtime.py")
    # Cleanup is registry-only; retained history/session storage must stay intact.
    assert "NavimowerHistory" not in source
    assert "SessionArchive" not in source


def test_beta58_readme_covers_options_custom_area_and_card_compatibility() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Settings -> Devices & services -> Navimower -> Configure" in readme
    assert "temporarily **merge" in readme.lower() or "temporarily merge" in readme.lower()
    assert "Off-limit" in readme
    assert "Navimower Map Card 0.3.5 requires Navimower integration 0.4.3 or newer" in readme
    assert "Reset schedule progress" in readme
