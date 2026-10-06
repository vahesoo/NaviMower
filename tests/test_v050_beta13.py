"""Release regressions for Navimower 0.5.0-beta13."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
CAPABILITIES = COMPONENT / "capability_extensions.py"
NUMBER = COMPONENT / "number.py"


def _cutting_height_call() -> ast.Call:
    tree = ast.parse(CAPABILITIES.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        key = keywords.get("key")
        if (
            isinstance(key, ast.Constant)
            and key.value == "cutting_height"
        ):
            return node
    raise AssertionError("cutting_height number description not found")


def _keyword_constant(call: ast.Call, name: str):
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    raise AssertionError(f"{name} keyword not found")


def test_beta13_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta13"

    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta13.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "decimal string",
        "cloud",
        "iot_set",
        "H215",
        "i215",
        "X390",
    ):
        assert marker in notes


def test_cutting_height_device_write_is_decimal_text() -> None:
    call = _cutting_height_call()
    assert _keyword_constant(call, "write_key") == "height"
    assert _keyword_constant(call, "robot_hex") is False


def test_generic_number_writer_keeps_cloud_numeric() -> None:
    source = NUMBER.read_text(encoding="utf-8")

    # robot_hex=False + robot_numeric=False intentionally selects str(wire)
    # for the immediate mower command.
    assert "elif desc.robot_numeric:" in source
    assert "robot_value = str(wire)" in source

    # Cutting height does not opt into cloud_hex/cloud_string, so the generic
    # default remains the bare integer used by save_setting_iot.
    assert "else:\n            cloud_value = wire" in source
    assert "self.coordinator.client.save_setting_iot" in source
