"""Static regressions for the v0.3.1+ integration features."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components/navimower"


def test_native_diagnostics_entrypoint_exists() -> None:
    source = (COMPONENT / "diagnostics.py").read_text()
    tree = ast.parse(source)
    names = {node.name for node in tree.body if isinstance(node, ast.AsyncFunctionDef)}
    assert "async_get_config_entry_diagnostics" in names
    assert '"diagnostics_source": "home_assistant_download"' in source
    assert "async_build_diagnostics" not in source


def test_schedule_master_uses_proven_start_plan_encoding() -> None:
    source = (COMPONENT / "switch.py").read_text()
    assert 'key="mowing_schedule_enabled"' in source
    assert 'translation_key="mowing_schedule_enabled"' in source
    assert 'write_key="startPlan"' in source
    assert "iot=True" in source
    assert "numeric=False" in source
    assert "robot_numeric=False" in source


def test_coordinator_exposes_schedule_and_shared_map_resolver() -> None:
    source = (COMPONENT / "coordinator.py").read_text()
    assert "resolve_map_identifiers" in source
    assert '"schedule_enabled": schedule_enabled' in source
    assert '"schedule_enabled": (data.get("settings") or {}).get(' in source
    assert '_find(set_list, "startPlan", "start_plan")' in source


def test_schedule_translation_exists() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text())
    assert strings["entity"]["switch"]["mowing_schedule_enabled"]["name"] == "Mowing schedule enabled"


def test_manifest_version() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"]


def test_diagnostics_sanitizer_redacts_sensitive_identifiers() -> None:
    source = (COMPONENT / "diagnostics_sanitize.py").read_text()
    ast.parse(source)
    assert "def sanitize" in source
    assert "def _is_sensitive_key" in source
    for key in (
        '"oauth_device_id"',
        '"vehicle_sn"',
        '"serial_number"',
        '"latitude"',
        '"longitude"',
        '"access_token"',
        '"refresh_token"',
        '"email"',
        '"ssid"',
        '"mac"',
    ):
        assert key in source
    assert 'return "<redacted>"' in source


def test_diagnostics_sanitizer_bounds_large_values_and_urls() -> None:
    source = (COMPONENT / "diagnostics_sanitize.py").read_text()
    ast.parse(source)
    assert '"_omitted": "large_string"' in source
    assert '"length": len(value)' in source
    assert '"sha256": hashlib.sha256(raw).hexdigest()' in source
    assert "def _safe_url" in source
    assert 'urlunsplit((parsed.scheme, location, parsed.path, "", ""))' in source
    assert "len(value) > 16_384" in source
