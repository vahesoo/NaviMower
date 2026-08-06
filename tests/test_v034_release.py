"""Stable v0.3.4 and next-beta regressions for Navimower."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_current_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.0-beta2"
    notes = (ROOT / ".github" / "release-notes" / "0.4.0-beta2.md").read_text()
    assert notes.startswith("title: Navimower 0.4.0-beta2")
    assert "include_sessions=0" in notes
    assert "include_daily_trails=0" in notes
    assert "H215" in notes
    assert "X390" in notes

    beta1_notes = (ROOT / ".github" / "release-notes" / "0.4.0-beta1.md").read_text()
    assert beta1_notes.startswith("title: Navimower 0.4.0-beta1")
    assert "completed-session" in beta1_notes

    stable_notes = (ROOT / ".github" / "release-notes" / "0.3.4.md").read_text()
    assert stable_notes.startswith("title: Navimower 0.3.4")


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


def test_v040_beta1_completed_session_archive_contract() -> None:
    svg = (COMPONENT / "session_svg.py").read_text()
    archive = (COMPONENT / "session_archive.py").read_text()
    api = (COMPONENT / "map_api.py").read_text()
    setup = (COMPONENT / "__init__.py").read_text()

    assert "SESSION_SVG_ARCHIVE_VERSION = 1" in svg
    assert '"fill_rule": "evenodd"' in svg
    assert '"swath_width_m": SWATH_WIDTH_M' in svg
    assert '"travel"' in svg
    assert "MQTT_CUTTING_ACTIONS" in svg
    assert "render_matches_session" in archive
    assert "async_add_executor_job" in archive
    assert "/api/navimower/session-render/{entry_id}/{session_id}" in api
    assert "session_render_api_path_template" in api
    assert "SessionArchiveManager" in setup
    ast.parse(svg)
    ast.parse(archive)
    ast.parse(api)
    ast.parse(setup)


def test_v040_beta2_lightweight_map_payload_contract() -> None:
    api = (COMPONENT / "map_api.py").read_text()

    assert '_FALSE_QUERY_VALUES = frozenset({"0", "false", "no", "off"})' in api
    assert 'include_sessions=_query_enabled(request, "include_sessions")' in api
    assert 'request, "include_daily_trails"' in api
    assert "if include_sessions and include_daily_trails:" in api
    assert "return await coordinator.async_map_payload()" in api
    assert "await coordinator.history.async_card_sessions() if include_sessions else []" in api
    assert "if include_daily_trails:" in api
    assert 'payload.pop("sessions", None)' not in api
    assert '"sessions",' in api
    assert 'payload.pop("daily_trails", None)' in api
    assert 'payload.pop("daily_trails_revision", None)' in api
    ast.parse(api)
