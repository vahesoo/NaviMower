"""Regression guards for Navimower 0.4.4-beta11 diagnostics tools."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _source(name: str) -> str:
    text = (COMPONENT / name).read_text(encoding="utf-8")
    ast.parse(text)
    return text


def test_beta11_release_notes_and_minimum_version() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    assert version.startswith("0.4.4-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 11
    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta11.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "local-frame",
        "relearn_georeference",
        "export_raw_data",
        "Download diagnostics",
    ):
        assert phrase in notes


def test_frame_diagnostics_compare_map_cloud_and_mqtt() -> None:
    source = _source("georeference_tools.py")
    for marker in (
        'geometry.get("station")',
        'cloud, "posture_x", "posture_y"',
        'mqtt, "x", "y"',
        '"private_minus_mqtt"',
        '"docked_private_minus_map_station"',
        '"docked_mqtt_minus_map_station"',
        '"report_time"',
        '"pose_time"',
    ):
        assert marker in source


def test_georeference_relearn_is_scoped_to_calibration() -> None:
    source = _source("georeference_tools.py")
    assert 'geometry.pop("_georeference_calibration", None)' in source
    assert 'geometry.pop("georeference", None)' in source
    assert 'geometry.get("_vendor_georeference")' in source
    assert "async_request_refresh" in source
    for forbidden in ("history.async_remove", "reset_schedule", "mow_zones", "client.dock"):
        assert forbidden not in source


def test_beta11_development_capture_surfaces_are_now_retired() -> None:
    """Beta11 notes stay historical while production no longer ships raw capture."""
    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta11.md").read_text(
        encoding="utf-8"
    )
    assert "export_raw_data" in notes
    assert "Download diagnostics" in notes
    assert not (COMPONENT / "raw_export.py").exists()
    assert not (COMPONENT / "raw_mqtt_semantics.py").exists()

    services = _source("services.py")
    yaml = (COMPONENT / "services.yaml").read_text(encoding="utf-8")
    assert "SERVICE_EXPORT_RAW_DATA" not in services
    assert "async_export_raw_data" not in services
    assert "export_raw_data:" not in yaml


def test_download_diagnostics_remain_sanitized() -> None:
    diagnostics = _source("diagnostics.py")
    assert '"diagnostics_source": "home_assistant_download"' in diagnostics
    assert '"raw_cache_summary": _raw_cache_summary(raw)' in diagnostics
    assert '"raw_payloads_included": False' in diagnostics
    assert "export_raw_data" not in diagnostics


def test_runtime_keeps_frame_semantics_without_raw_mqtt_capture() -> None:
    runtime = _source("runtime.py")
    assert "install_georeference_semantics()" in runtime
    assert "install_georeference_diagnostics_semantics()" in runtime
    assert runtime.index("install_georeference_semantics()") < runtime.index(
        "install_georeference_diagnostics_semantics()"
    )
    assert "install_raw_mqtt_semantics" not in runtime
