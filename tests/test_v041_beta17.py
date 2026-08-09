"""Regression contracts for Navimower 0.4.1-beta17."""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _load_route_dedupe():
    path = COMPONENT / "route_dedupe.py"
    spec = importlib.util.spec_from_file_location("navimower_route_dedupe_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_beta17_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta17"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta17.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta17")
    for phrase in (
        "i208 AWD",
        "Global cutting height",
        "charging",
        "route/history",
        "Eco mode",
    ):
        assert phrase in notes


def test_beta17_runtime_exposes_i2_awds_observed_settings() -> None:
    source = (COMPONENT / "beta17_runtime.py").read_text()
    ast.parse(source)
    for model in ("i205 AWD", "i206 AWD", "i208 AWD", "i210 AWD"):
        assert model in source
    for vendor_key in (
        "powerSaveShutdownSwitch",
        "narrowZoneAdaptSwitch",
        "advancedSlopeMode",
        "grassPatternEnhancement",
        "progressRetentionSwitch",
        "progressRetentionDuration",
        "cycleMowingTimeSetting",
        "terrainAdaptSwitch",
        "edgeSense",
        "edgeSenselevel",
        "lightIntensity",
        "nightLightLevel",
        "rtkDataSource",
    ):
        assert vendor_key in source
    for manual_name in (
        "Eco mode",
        "Channel Obstacle Avoidance",
        "Animal friendly",
        "Traction Control System (TCS)",
        "Camera-assisted positioning",
        "Positioning mode",
    ):
        assert manual_name in source


def test_beta17_battery_limits_and_global_cutting_height_are_dynamic() -> None:
    source = (COMPONENT / "beta17_runtime.py").read_text()
    assert 'key="return_battery_level"' in source
    assert "native_max_value=20" in source
    assert 'key="charging_limit"' in source
    assert "native_min_value=70" in source
    for key in (
        "returnBatteryLevelMin",
        "returnBatteryLevelMax",
        "chargingLimitMin",
        "chargingLimitMax",
        "mowingHeightList",
    ):
        assert key in source
    assert 'key="cutting_height"' in source
    assert 'name="Global cutting height"' in source
    assert 'raw_read_key="height"' in source
    assert 'write_key="height"' in source
    assert "_height_step" in source


def test_route_point_metadata_only_changes_coalesce_and_enrich() -> None:
    module = _load_route_dedupe()
    points = [[1786303702000, -93.0, -8.75, -1.885, "error", 4, 5, None]]
    appended = module.append_or_coalesce(
        points,
        [1786303702000, -93.0, -8.75, -1.885, "error", None, None, 73],
    )
    assert appended is False
    assert len(points) == 1
    assert points[0][5:8] == [4, 5, 73]

    appended = module.append_or_coalesce(
        points,
        [1786303702000, -93.0, -8.75, -1.885, "mowing", 4, 5, 73],
    )
    assert appended is True
    assert len(points) == 2


def test_persisted_beta15_beta16_route_duplicates_are_compacted() -> None:
    module = _load_route_dedupe()
    raw = [
        [1786303702000, -93.0, -8.75, -1.885, "error", 4, 5, None],
        [1786303702000, -93.0, -8.75, -1.885, "error", None, None, None],
        [1786303702000, -93.0, -8.75, -1.885, "error", 4, 5, 73],
        [1786303702000, -93.0, -8.75, -1.885, "mowing", 4, 5, 73],
    ]
    compacted = module.compact_route_points(raw)
    assert len(compacted) == 2
    assert compacted[0][5:8] == [4, 5, 73]
    assert compacted[1][4] == "mowing"


def test_beta17_runtime_is_chained_after_beta16() -> None:
    source = (COMPONENT / "beta16_runtime.py").read_text()
    assert "from .beta17_runtime import install_beta17_runtime" in source
    assert source.rindex("_install_error_sensor_attributes()") < source.rindex(
        "install_beta17_runtime()"
    )
