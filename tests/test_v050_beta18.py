"""Release regressions for Navimower 0.5.0-beta18."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta18_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta18"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta18.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "35 -> 53",
        "10 -> 16",
        '35% -> `"23"`',
        "minimum: 10%",
        "step: 10%",
    ):
        assert marker in notes


def test_sound_level_uses_hex_device_and_decimal_string_cloud() -> None:
    source = (COMPONENT / "number.py").read_text(encoding="utf-8")
    block = source.split('key="sound_level"', 1)[1].split(
        "NavimowNumberDescription(", 1
    )[0]
    assert "native_min_value=10" in block
    assert "native_max_value=100" in block
    assert "native_step=10" in block
    assert "robot_hex=True" in block
    assert "cloud_string=True" in block
    assert 'raw_read_key="volumeSetting"' in block
    assert 'write_key="volumeSetting"' in block
    ast.parse(source)


def test_number_writer_encodes_hex_device_and_decimal_cloud() -> None:
    source = (COMPONENT / "number.py").read_text(encoding="utf-8")
    assert 'robot_value: int | str = f"{wire:02X}"' in source
    assert 'cloud_value = str(wire)' in source
