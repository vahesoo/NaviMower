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


def test_raw_export_preserves_full_vendor_and_map_values() -> None:
    source = _source("raw_export.py")
    for marker in (
        '"navimower-raw-data-v1"',
        '"private_cloud_fresh"',
        '"private_cloud_cached"',
        '"map_geometry_decoded"',
        '"mqtt_raw_last_messages"',
        '"coordinator_snapshot"',
        '"local_frame_check"',
        '"map_detail_plain"',
        '"map_detail_compress"',
        '"station_map"',
        '"vehicle_config"',
        '"navimower_raw_latest.json"',
    ):
        assert marker in source
    assert "sanitize(" not in source


def test_raw_export_is_read_only() -> None:
    source = _source("raw_export.py")
    for forbidden in (
        "mow_zones(",
        "save_setting(",
        "send_setting_device(",
        "set_day_schedule(",
        "client.pause(",
        "client.dock(",
        "client.resume(",
    ):
        assert forbidden not in source


def test_exact_latest_mqtt_payload_is_retained_bounded() -> None:
    source = _source("raw_mqtt_semantics.py")
    assert 'raw.decode("utf-8", errors="replace")' in source
    assert '"payload_base64"' in source
    assert "base64.b64encode(raw)" in source
    assert '"payload_bytes"' in source
    assert "while len(cache) > 64" in source
    assert "sanitize_discovery_payload" not in source


def test_services_expose_relearn_and_raw_export() -> None:
    source = _source("services.py")
    yaml = (COMPONENT / "services.yaml").read_text(encoding="utf-8")
    for marker in (
        'SERVICE_RELEARN_GEOREFERENCE = "relearn_georeference"',
        'SERVICE_EXPORT_RAW_DATA = "export_raw_data"',
        "async_relearn_georeference",
        "async_export_raw_data",
    ):
        assert marker in source
    assert "relearn_georeference:" in yaml
    assert "export_raw_data:" in yaml
    assert "unredacted" in yaml.lower()


def test_download_diagnostics_remain_sanitized() -> None:
    diagnostics = _source("diagnostics.py")
    assert '"diagnostics_source": "home_assistant_download"' in diagnostics
    assert '"raw": sanitize(raw_for_diagnostics)' in diagnostics
    assert "export_raw_data" not in diagnostics


def test_runtime_installs_frame_and_raw_mqtt_semantics() -> None:
    runtime = _source("runtime.py")
    assert "install_georeference_semantics()" in runtime
    assert "install_georeference_diagnostics_semantics()" in runtime
    assert runtime.index("install_georeference_semantics()") < runtime.index(
        "install_georeference_diagnostics_semantics()"
    )
    assert "install_raw_mqtt_semantics()" in runtime
