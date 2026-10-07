"""Release regressions for Navimower 0.5.0-beta16."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta16_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta16"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta16.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Work mode persistence field test",
        "mower `2`, cloud `2`",
        "Device-command outcome diagnostics",
        "Per-setting persistence verification",
    ):
        assert marker in notes


def test_work_mode_uses_numeric_mower_and_cloud_values() -> None:
    source = (COMPONENT / "select.py").read_text(encoding="utf-8")
    block = source.split('key="work_mode"', 1)[1].split(
        "NavimowSelectDescription(", 1
    )[0]
    assert '"standard": 2' in block
    assert '"efficient": 3' in block
    assert '"precision": 4' in block
    assert "robot_numeric=True" in block
    assert "cloud_string=True" not in block
    assert "cloud_hex=True" not in block
    ast.parse(source)


def test_setting_verification_is_per_key() -> None:
    source = (COMPONENT / "setting_write.py").read_text(encoding="utf-8")
    assert 'getattr(coordinator, "_setting_readback_tasks", None)' in source
    assert 'getattr(coordinator, "_setting_command_tasks", None)' in source
    assert 'store["keys"][str(key)]' in source
    assert 'phase="readback_15s"' in source
    assert 'phase="persistence_75s"' in source
    assert "SETTING_PERSISTENCE_DELAY_SECONDS = 75.0" in source
    ast.parse(source)


def test_setting_writer_checks_device_command_response_safely() -> None:
    source = (COMPONENT / "setting_write.py").read_text(encoding="utf-8")
    assert 'getattr(func, "__name__", "") != "send_setting_device"' in source
    assert "command_status" in source
    assert "_command_response_summary" in source
    assert '"command_number_received": command_number is not None' in source
    assert '"outcome": "response_received"' in source
    assert "response_type" in source
    assert "top_level_keys" in source
    assert "status_fields" in source
    ast.parse(source)


def test_diagnostics_exposes_only_cached_verification_summary() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert '"setting_write_verification"' in diagnostics
    assert "command_status(" not in diagnostics
