"""Regression contract for Navimower 0.4.2-beta7 regional routing/capabilities."""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _load_regions():
    path = COMPONENT / "api" / "regions.py"
    spec = importlib.util.spec_from_file_location("navimower_regions_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_beta7_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.2-beta7"
    notes = (ROOT / ".github" / "release-notes" / "0.4.2-beta7.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.2-beta7")
    assert "region" in notes.lower()
    assert "capability" in notes.lower()
    assert "mqtt" in notes.lower()


def test_beta7_region_table_covers_observed_account_regions() -> None:
    regions = _load_regions()
    assert set(regions.REGIONS) == {"fra", "sg", "us", "bj"}
    assert regions.canonical_region("EU") == "fra"
    assert regions.canonical_region("sea") == "sg"
    assert regions.canonical_region("ore") == "us"
    assert regions.mower_hosts("us")[0] == "navimow-fra.ninebot.com"
    assert "api-passport-ore.ninebot.com" in regions.passport_hosts("us")


def test_beta7_passport_resolves_region_before_password_login() -> None:
    path = COMPONENT / "api" / "passport.py"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    lookup = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "lookup_region"
    )
    lookup_text = ast.get_source_segment(text, lookup) or ""
    assert '"/v3/region"' in lookup_text
    assert 'method="GET"' in lookup_text
    assert '"account": email' in lookup_text
    assert '"password"' not in lookup_text
    assert "RESULT_ACCOUNT_NOT_EXISTS" in text
    assert "lookup_region(username)" in text


def test_beta7_private_client_uses_per_instance_regional_host() -> None:
    api = (COMPONENT / "api" / "__init__.py").read_text(encoding="utf-8")
    assert "self._host" in api
    assert "mower_host_candidates" in api
    assert "mower_hosts(self._region)" in api
    assert "def mower_login" in api
    assert '"host": self._host' in api
    assert "https://{self._host}{path}" in api
    assert "_RESOLVED_MOWER_HOSTS" in api

    bridge = (COMPONENT / "private_cloud_region.py").read_text(encoding="utf-8")
    assert "CONF_MOWER_HOST" in bridge
    assert 'source="persisted"' in bridge
    assert "original_persist(self)" in bridge
    assert '"smart_home_mqtt_routing": "api_provided_mqttHost_or_mqttUrl"' in bridge


def test_beta7_capability_profile_is_positive_evidence_not_sensor_pruning() -> None:
    profile = (COMPONENT / "capability_profile.py").read_text(encoding="utf-8")
    assert '"policy": "positive_evidence_only"' in profile
    assert '"endpoints"' in profile
    assert '"setting_key_paths"' in profile
    assert '"observed"' in profile
    assert '"constraints"' in profile
    assert "is_h1_generation" in profile
    assert '"supported": False' in profile
    assert "async_remove" not in profile

    sensors = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    assert "async_add_entities(entities)" in sensors


def test_beta7_diagnostics_exposes_region_and_capability_profile() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert '"private_cloud_region"' in diagnostics
    assert '"capabilities"' in diagnostics
    assert "private_cloud_region_diagnostics(coordinator)" in diagnostics
    assert "build_capability_profile(data)" in diagnostics
    assert "api_provided_mqttHost_or_mqttUrl" in (
        COMPONENT / "private_cloud_region.py"
    ).read_text(encoding="utf-8")
