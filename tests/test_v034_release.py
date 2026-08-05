"""Stable-release regressions for Navimower v0.3.4."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_stable_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.3.4"
    notes = (ROOT / ".github" / "release-notes" / "0.3.4.md").read_text()
    assert notes.startswith("title: Navimower 0.3.4")
    assert "Primary development and live bidirectional setting tests" in notes
    assert "H215" in notes
    assert "X390" in notes


def test_setting_platforms_gate_and_clean_unsupported_entities() -> None:
    for filename, domain in (
        ("switch.py", "switch"),
        ("select.py", "select"),
        ("number.py", "number"),
    ):
        source = (COMPONENT / filename).read_text()
        assert "from homeassistant.helpers import entity_registry as er" in source
        assert "_remove_unsupported_registry_entities" in source
        assert "if _set_list(data) is not None:" in source
        assert f'"{domain}", DOMAIN, f"{{coordinator.sn}}_{{desc.key}}"' in source
        assert "registry.async_remove(entity_id)" in source
        ast.parse(source)


def test_setting_switches_are_presence_gated_and_enabled() -> None:
    source = (COMPONENT / "switch.py").read_text()
    present = source.split(
        "def _present(desc: NavimowSwitchDescription, data: dict) -> bool:", 1
    )[1].split("def _remove_unsupported_registry_entities", 1)[0]
    assert "if desc.proven" not in present
    assert "_model_supported(desc, data)" in present
    assert "_read_raw_value(desc, data) is not None" in present
    assert "enabled_default: bool = True" in source
    assert "description.enabled_default" in source


def test_supported_selects_and_numbers_are_enabled_by_default() -> None:
    select_source = (COMPONENT / "select.py").read_text()
    number_source = (COMPONENT / "number.py").read_text()
    assert "self._attr_entity_registry_enabled_default = True" in select_source
    assert "enabled_default: bool = True" in number_source
    geo = number_source.split('key="geo_fence_radius"', 1)[1].split(
        "NavimowNumberDescription(", 1
    )[0]
    assert "enabled_default=False" not in geo


def test_model_specific_brightness_and_h215_lab_gates() -> None:
    select_source = (COMPONENT / "select.py").read_text()
    switch_source = (COMPONENT / "switch.py").read_text()

    h215 = select_source.split('key="night_light_level"', 1)[1].split(
        "NavimowSelectDescription(", 1
    )[0]
    assert 'name="Night light brightness"' in h215
    assert 'raw_read_key="lightIntensity"' in h215
    assert 'models=("H215",)' in h215

    x390 = select_source.split('key="light_brightness"', 1)[1].split(
        "NavimowSelectDescription(", 1
    )[0]
    assert 'name="Brightness"' in x390
    assert 'raw_read_key="nightLightLevel"' in x390
    assert 'models=("X390",)' in x390

    for key in ("terrain_adapt", "edge_sense"):
        block = switch_source.split(f'key="{key}"', 1)[1].split(
            "NavimowSwitchDescription(", 1
        )[0]
        assert 'models=("H215",)' in block

    edge_mode = select_source.split('key="edge_sense_mode"', 1)[1].split(
        "NavimowSelectDescription(", 1
    )[0]
    assert 'models=("H215",)' in edge_mode


def test_release_workflow_supports_stable_and_prerelease_tags() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-prerelease.yaml").read_text()
    assert "name: Publish integration release" in workflow
    assert 'RELEASE_ARGS+=(--prerelease)' in workflow
    assert 'gh release create "${TAG}"' in workflow
    assert '"${RELEASE_ARGS[@]}"' in workflow
    assert "Skip stable release" not in workflow


def test_readme_documents_entities_models_and_testing_scope() -> None:
    readme = (ROOT / "README.md").read_text()
    assert "### Entity reference and model support" in readme
    assert "Primary field testing has been performed on an **H215**" in readme
    assert "Night light brightness" in readme
    assert "Terrain adapt" in readme
    assert "### v0.3.4" in readme
