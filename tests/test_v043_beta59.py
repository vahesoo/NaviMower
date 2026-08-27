"""Dependency-free regression coverage for 0.4.3-beta59."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _source(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


def test_beta59_zone_cleanup_parses_longest_metric_first() -> None:
    source = _source("zone_entity_cleanup.py")
    assert "_ZONE_METRIC_PARSE_ORDER" in source
    assert "sorted(_ZONE_METRIC_KEYS, key=len, reverse=True)" in source
    assert "for metric in _ZONE_METRIC_PARSE_ORDER" in source
    assert "except (TypeError, ValueError):\n            continue" in source


def test_beta59_schedule_reset_button_is_a_control_not_configuration() -> None:
    source = _source("button.py")
    assert '_attr_name = "Reset schedule progress"' in source
    assert "EntityCategory.CONFIG" not in source
    assert "from homeassistant.const import EntityCategory" not in source


def test_beta59_gate_automation_documents_tested_single_target_scope() -> None:
    guide = (ROOT / "docs" / "GATE_AUTOMATION.md").read_text(encoding="utf-8")
    assert "Gate required" in guide
    assert "Custom Area" in guide
    assert "Navimower Schedule" in guide
    assert "manually started mowing task containing **one mowing zone**" in guide
    assert "extends **slightly into the mowing zone**" in guide
    assert "lawn_mower.pause" in guide
    assert "lawn_mower.start_mowing" in guide
    assert "lawn_mower.dock" in guide
    assert "state_code') == '0211'" in guide


def test_beta59_docs_drop_obsolete_multi_mower_beta_guide() -> None:
    assert (ROOT / "docs" / "MULTI_MOWER.md").is_file()
    assert not (ROOT / "docs" / "MULTI_MOWER_BETA.md").exists()
    guide = (ROOT / "docs" / "MULTI_MOWER.md").read_text(encoding="utf-8")
    assert "selects it automatically" in guide
    assert "Download diagnostics" in guide
    assert "export_diagnostics" in guide  # explicitly documented as retired


def test_beta59_architecture_guard_lists_current_semantic_layers() -> None:
    source = (ROOT / "tests" / "test_runtime_architecture.py").read_text(encoding="utf-8")
    for filename in (
        "schedule_pause_semantics.py",
        "setup_flow_semantics.py",
        "zone_entity_cleanup.py",
    ):
        assert f'"{filename}"' in source
    for call in (
        "install_schedule_pause_semantics()",
        "install_setup_flow_semantics()",
        "install_zone_entity_cleanup()",
    ):
        assert f'"{call}"' in source


def test_beta59_release_contract_is_retained() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta59.md").read_text(
        encoding="utf-8"
    )
    assert notes.startswith("title: Navimower 0.4.3-beta59")
    assert "Mowed area" in notes
    assert "Reset schedule progress" in notes
    assert "Gate required" in notes
