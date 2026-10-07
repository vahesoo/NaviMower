"""Release regressions for Navimower 0.5.0-beta17."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta17_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta17"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta17.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Sound level control",
        'volumeSetting="100"',
        'volumeSetting="12"',
        "0..100%",
        "15 s / 75 s persistence trace",
    ):
        assert marker in notes


def test_sound_level_is_capability_driven_percentage_slider() -> None:
    source = (COMPONENT / "number.py").read_text(encoding="utf-8")
    block = source.split('key="sound_level"', 1)[1].split(
        "NavimowNumberDescription(", 1
    )[0]
    assert 'name="Sound level"' in block
    assert "native_unit_of_measurement=PERCENTAGE" in block
    assert "native_min_value=0" in block
    assert "native_max_value=100" in block
    assert "native_step=1" in block
    assert 'raw_read_key="volumeSetting"' in block
    assert 'write_key="volumeSetting"' in block
    assert "robot_hex=False" in block
    assert "cloud_string=True" in block
    ast.parse(source)


def test_sound_level_uses_string_device_and_cloud_encoding() -> None:
    source = (COMPONENT / "number.py").read_text(encoding="utf-8")
    assert 'robot_value = str(wire)' in source
    assert 'cloud_value = str(wire)' in source
