"""Regression contracts for Navimower 0.4.1-beta18."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _load_position_fallback():
    path = COMPONENT / "position_fallback.py"
    spec = importlib.util.spec_from_file_location("navimower_position_fallback_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_beta18_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta18"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta18.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta18")
    for phrase in (
        "Fresh official MQTT X/Y remains authoritative",
        "private-cloud X/Y",
        "Two distinct fresh vendor position reports",
        "shared private-cloud account",
    ):
        assert phrase in notes


def test_position_resolver_always_prefers_mqtt() -> None:
    module = _load_position_fallback()
    result = module.choose_position(
        mqtt_position={"x": 1.0, "y": 2.0},
        mqtt_age=3.0,
        cloud_position={"x": 9.0, "y": 9.0},
        cloud_report_time=990.0,
        now_epoch=1000.0,
    )
    assert result["source"] == "mqtt"
    assert result["position"] == {"x": 1.0, "y": 2.0}
    assert result["gate_usable"] is True
    assert result["stale"] is False


def test_fresh_cloud_is_gate_usable_when_mqtt_pose_is_missing() -> None:
    module = _load_position_fallback()
    result = module.choose_position(
        mqtt_position=None,
        mqtt_age=None,
        cloud_position={"x": -13.25, "y": -43.0},
        cloud_report_time=980.0,
        now_epoch=1000.0,
    )
    assert result["source"] == "private_cloud"
    assert result["position"] == {"x": -13.25, "y": -43.0}
    assert result["age"] == 20.0
    assert result["gate_usable"] is True
    assert result["stale"] is False


def test_stale_cloud_remains_display_only() -> None:
    module = _load_position_fallback()
    result = module.choose_position(
        mqtt_position=None,
        mqtt_age=None,
        cloud_position={"x": -13.25, "y": -43.0},
        cloud_report_time=900.0,
        now_epoch=1000.0,
    )
    assert result["source"] == "private_cloud"
    assert result["position"] is not None
    assert result["age"] == 100.0
    assert result["gate_usable"] is False
    assert result["stale"] is True


def test_beta18_runtime_chains_and_protects_cloud_gate_release() -> None:
    beta16 = (COMPONENT / "beta16_runtime.py").read_text()
    runtime = (COMPONENT / "beta18_runtime.py").read_text()
    assert "from .beta18_runtime import install_beta18_runtime" in beta16
    assert beta16.rindex("install_beta17_runtime()") < beta16.rindex(
        "install_beta18_runtime()"
    )
    assert "_risky_cloud_gate_transition" in runtime
    assert "count < 2" in runtime
    assert 'context.get("gate_usable")' in runtime
    assert '"current_physical_zone_position_source"' in runtime
    assert '"cloud_fallback"' in runtime
    assert "outside_count >= 2" in runtime


def test_readme_documents_shared_account_and_fallback_rules() -> None:
    readme = (ROOT / "README.md").read_text()
    assert "Multiple mower config entries may use" in readme
    assert "dedicated private-cloud" in readme
    assert "stable app/device identity" in readme
    assert "currently unsupported" not in readme
    assert "private-cloud" in readme and "report_time" in readme
    assert "two distinct" in readme.lower()
