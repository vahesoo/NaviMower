"""Regression contracts for Navimower 0.4.1-beta14."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _load(name: str, filename: str):
    path = COMPONENT / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_beta14_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta14"
    assert all(not requirement.startswith("zstandard") for requirement in manifest["requirements"])
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta14.md").read_text()
    assert "vehicleErrorData" in notes
    assert "vehicleEventData" in notes
    assert "180D" in notes


def test_beta14_builds_exact_catalog_for_fault_and_event_codes() -> None:
    catalog_module = _load("navimower_beta14_error_catalog", "error_catalog.py")
    inspection = {
        "decoded": {
            "decoded_data": {
                "vehicleErrorData": [
                    {
                        "error_code": "6113",
                        "title": "Failed to dock (error: 6113)",
                        "content": "Check the charging station.",
                        "level": "0",
                        "name": "",
                        "relate_id": "0",
                        "updatetime": 1782219533,
                    }
                ],
                "vehicleEventData": [
                    {
                        "error_code": "180d",
                        "title": "Mower got stuck or lifted",
                        "content": "Find the mower and place it on the lawn.",
                        "level": "0",
                        "name": "",
                        "relate_id": "0",
                        "updatetime": 1782219584,
                    }
                ],
                "warningData": [],
                "vehicle_update_time": "1784886451",
            }
        }
    }

    catalog = catalog_module.build_error_catalog(inspection)
    assert catalog["available"] is True
    assert catalog["code_count"] == 2
    assert catalog["section_counts"]["vehicleErrorData"] == 1
    assert catalog["section_counts"]["vehicleEventData"] == 1
    assert catalog_module.resolve_error_code(catalog, "6113")[0]["title"].startswith("Failed to dock")
    event = catalog_module.resolve_error_code(catalog, "180D")[0]
    assert event["section"] == "vehicleEventData"
    assert event["title"] == "Mower got stuck or lifted"
    assert catalog_module.resolve_error_code(catalog, "0302") == []


def test_beta14_passive_discovery_retains_error_notification_signals() -> None:
    discovery = _load("navimower_beta14_discovery", "discovery.py")
    summary = discovery.structure_summary(
        {
            "notificationType": "alarm",
            "eventCode": "180D",
            "title": "Mower got stuck or lifted",
            "message": "Failed to dock (error: 6113)",
            "nested": {"error_code": 1105, "faultReason": "blade motor stalled"},
            "device_id": "secret-device-id",
        }
    )
    observed = set(summary["observed_type_values"])
    assert "eventCode=180D" in observed
    assert "code_candidate=180D" in observed
    assert "code_candidate=6113" in observed
    assert "code_candidate=1105" in observed
    assert any(item.startswith("title=Mower got stuck or lifted") for item in observed)
    assert any(item.startswith("message=Failed to dock") for item in observed)
    assert not any("secret-device-id" in item for item in observed)


def test_beta14_catalog_is_cached_by_private_cloud_wrapper_contract() -> None:
    source = (COMPONENT / "api" / "__init__.py").read_text()
    assert "build_error_catalog(inspection)" in source
    assert "self._navimow_error_catalog = catalog" in source
    assert "def error_catalog" in source
    assert "def resolve_error_code" in source
    assert '"catalog": deepcopy(catalog)' in source
