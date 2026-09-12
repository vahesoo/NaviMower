"""Regression coverage for beta34 Map Card gate-area write support."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
CHANNEL = COMPONENT / "channel.py"
EDITOR = COMPONENT / "gate_area_editor.py"
SERVICES = COMPONENT / "services.py"
SERVICES_YAML = COMPONENT / "services.yaml"


def _load_editor_module():
    package_name = "navimower_gate_editor_beta34"
    package = types.ModuleType(package_name)
    package.__path__ = [str(COMPONENT)]
    sys.modules[package_name] = package

    channel_spec = importlib.util.spec_from_file_location(
        f"{package_name}.channel", CHANNEL
    )
    assert channel_spec is not None and channel_spec.loader is not None
    channel_module = importlib.util.module_from_spec(channel_spec)
    sys.modules[channel_spec.name] = channel_module
    channel_spec.loader.exec_module(channel_module)

    editor_spec = importlib.util.spec_from_file_location(
        f"{package_name}.gate_area_editor", EDITOR
    )
    assert editor_spec is not None and editor_spec.loader is not None
    editor_module = importlib.util.module_from_spec(editor_spec)
    sys.modules[editor_spec.name] = editor_module
    editor_spec.loader.exec_module(editor_module)
    return editor_module


def test_editor_accepts_three_or_more_points_and_calculates_bounds() -> None:
    editor = _load_editor_module()
    polygon = [[0, 0], [4, 0], [5, 2], [3, 4], [0, 3]]
    stored = editor.upsert_gate_area([], name="Front gate", polygon=polygon)

    assert len(stored) == 1
    area = stored[0]
    assert area["slug"] == "front_gate"
    assert area["polygon"] == [[float(x), float(y)] for x, y in polygon]
    assert (area["x_min"], area["x_max"], area["y_min"], area["y_max"]) == (
        0.0,
        5.0,
        0.0,
        4.0,
    )


def test_editor_updates_by_current_slug_and_deletes_without_duplicates() -> None:
    editor = _load_editor_module()
    first = editor.upsert_gate_area(
        [],
        name="Front gate",
        polygon=[[0, 0], [4, 0], [4, 4], [0, 4]],
    )
    updated = editor.upsert_gate_area(
        first,
        gate_area_id="front_gate",
        name="Front passage",
        polygon=[[1, 1], [5, 1], [5, 3], [1, 3]],
    )

    assert len(updated) == 1
    assert updated[0]["slug"] == "front_passage"
    assert updated[0]["x_min"] == 1.0
    assert editor.delete_gate_area(updated, "front_passage") == []


def test_editor_rejects_self_intersection_and_duplicate_slug() -> None:
    editor = _load_editor_module()
    with pytest.raises(ValueError, match="intersect itself"):
        editor.build_gate_area(
            "Bow tie",
            [[0, 0], [4, 4], [0, 4], [4, 0]],
        )

    stored = editor.upsert_gate_area(
        [],
        name="Front gate",
        polygon=[[0, 0], [4, 0], [4, 4], [0, 4]],
    )
    with pytest.raises(ValueError, match="already exists"):
        editor.upsert_gate_area(
            stored,
            name="Front gate",
            polygon=[[10, 10], [12, 10], [12, 12], [10, 12]],
        )


def test_services_persist_options_reload_entry_and_leave_mow_semantics_intact() -> None:
    source = SERVICES.read_text(encoding="utf-8")
    assert 'SERVICE_SET_GATE_AREA = "set_gate_area"' in source
    assert 'SERVICE_DELETE_GATE_AREA = "delete_gate_area"' in source
    assert "upsert_gate_area(" in source
    assert "delete_gate_area(" in source
    assert "options[OPT_CHANNELS] = channels" in source
    assert "hass.config_entries.async_update_entry(coordinator.entry, options=options)" in source
    assert "await hass.config_entries.async_reload(coordinator.entry.entry_id)" in source

    # The editor service must not accidentally alter the established mow command path.
    assert "coordinator.client.mow_zones" in source
    assert "coordinator.record_mow_command_result(result)" in source
    assert "self.coordinator.record_mow_command_result" not in source

    services_yaml = SERVICES_YAML.read_text(encoding="utf-8")
    assert "\nset_gate_area:\n" in services_yaml
    assert "\ndelete_gate_area:\n" in services_yaml
    assert "gate_area_id:" in services_yaml
    assert "3 to 64 unique points" in services_yaml


def test_beta34_release_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.4-beta34"
    notes = ROOT / ".github" / "release-notes" / "0.4.4-beta34.md"
    assert notes.is_file()
    assert notes.read_text(encoding="utf-8").startswith(
        "title: Navimower 0.4.4-beta34\n"
    )
