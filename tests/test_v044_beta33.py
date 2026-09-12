"""Regression coverage for the beta33 gate-area Options Flow hotfix."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
SEMANTICS = COMPONENT / "gate_area_polygon_semantics.py"


def test_polygon_selector_is_serializable_and_validation_is_outside_schema() -> None:
    source = SEMANTICS.read_text(encoding="utf-8")
    schema_start = source.index("def _channel_schema")
    schema_end = source.index("\ndef _invalid_polygon", schema_start)
    schema = source[schema_start:schema_end]

    assert 'schema[vol.Optional("polygon", default=default)] = TextSelector(' in schema
    assert "vol.All(" not in schema
    assert "_polygon_text" not in schema

    assert "def _invalid_polygon" in source
    assert "_polygon_text(user_input.get(\"polygon\", \"\"))" in source
    assert "NavimowOptionsFlow.async_step_channel_add = _async_step_channel_add" in source
    assert "NavimowOptionsFlow.async_step_channel_edit = _async_step_channel_edit" in source


def test_beta33_release_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.4-beta33"
    notes = ROOT / ".github" / "release-notes" / "0.4.4-beta33.md"
    assert notes.is_file()
    assert notes.read_text(encoding="utf-8").startswith(
        "title: Navimower 0.4.4-beta33\n"
    )
