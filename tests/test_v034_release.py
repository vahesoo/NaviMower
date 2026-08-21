from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _source(name: str) -> str:
    return (COMPONENT / name).read_text()


def test_v034_manifest_is_stable_release() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"].startswith("0.4.")


def test_v034_sensor_contract() -> None:
    sensor = _source("sensor.py")
    ast.parse(sensor)
    for key in (
        "battery",
        "status",
        "problem",
        "map_area",
        "map_mowed_area",
        "map_coverage",
        "task_mowed_area",
        "task_progress",
        "weekly_mowed_area",
        "global_cutting_height",
        "latest_notification",
        "heading",
        "position_x",
        "position_y",
        "target_zone",
        "zone_transition",
        "schedule",
    ):
        assert f'key="{key}"' in sensor


def test_v034_switch_contract() -> None:
    switch = _source("switch.py")
    ast.parse(switch)
    for key in (
        "mowing_schedule_enabled",
        "night_mow",
        "mowing_cycle",
        "rain_detection",
        "rain_sensor",
        "weather_rain",
        "rain_delay_mode",
        "frost_delay",
        "snow_delay",
        "storm_delay",
        "high_temp_delay",
        "sound",
        "power_saving",
        "do_not_disturb",
        "night_light",
        "child_lock",
        "lift_alarm",
        "geo_fence_alarm",
        "efls",
        "obstacle_avoidance",
        "traction_control",
        "animal_protection",
        "terrain_adapt",
        "edge_sense",
    ):
        assert f'key="{key}"' in switch


def test_v034_number_contract() -> None:
    number = _source("number.py")
    ast.parse(number)
    for key in (
        "charging_limit",
        "return_battery_level",
        "rain_delay",
        "global_cutting_height",
        "light_brightness",
        "night_light_brightness",
    ):
        assert f'key="{key}"' in number


def test_v034_select_contract() -> None:
    select = _source("select.py")
    ast.parse(select)
    for key in (
        "work_mode",
        "mowing_direction",
        "positioning_mode",
    ):
        assert f'key="{key}"' in select


def test_v034_lawn_mower_contract() -> None:
    mower = _source("lawn_mower.py")
    ast.parse(mower)
    assert "LawnMowerEntityFeature.START_MOWING" in mower
    assert "LawnMowerEntityFeature.PAUSE" in mower
    assert "LawnMowerEntityFeature.DOCK" in mower


def test_v034_services_contract() -> None:
    services = _source("services.py")
    ast.parse(services)
    for handler in (
        "async_handle_mow_zones",
        "async_handle_resume",
        "async_handle_mark_notification_read",
        "async_handle_mark_all_notifications_read",
    ):
        assert handler in services


def test_v034_map_card_api_contract() -> None:
    api = _source("map_api.py")
    ast.parse(api)
    assert "async_get_map_card_data" in api
    assert "async_get_map_card_session" in api


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
    # README is current-state documentation; release-by-release history belongs
    # in CHANGELOG.md rather than being duplicated as embedded v0.x sections.
    assert "[CHANGELOG.md](CHANGELOG.md)" in readme
    assert "### v0.3.4" not in readme


def test_v040_beta1_completed_session_archive_contract() -> None:
    svg = (COMPONENT / "session_svg.py").read_text()
    archive = (COMPONENT / "session_archive.py").read_text()
    api = (COMPONENT / "map_api.py").read_text()
    setup = (COMPONENT / "__init__.py").read_text()

    assert "SESSION_SVG_ARCHIVE_VERSION = 2" in svg
    assert '"fill_rule": "evenodd"' in svg
    assert '"swath_width_m": swath_width' in svg
    assert '"travel"' in svg
    assert "MQTT_CUTTING_ACTIONS" in svg
    assert "async_list_sessions" in archive
    assert "async_get_session" in archive
    assert "async_get_map_card_data" in api
    assert "async_get_map_card_session" in api
    assert "NavimowerSessionArchive" in setup
