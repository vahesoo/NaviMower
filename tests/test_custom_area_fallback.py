import importlib.util
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "navimower"
    / "custom_area_fallback.py"
)
spec = importlib.util.spec_from_file_location("navimower_custom_area_fallback", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
resolve_custom_area_presence = module.resolve_custom_area_presence


def test_mqtt_area_state_is_immediate():
    state, memory = resolve_custom_area_presence(
        {},
        inside=True,
        source="mqtt",
        usable=True,
    )
    assert state is True
    assert memory["value"] is True

    state, memory = resolve_custom_area_presence(
        memory,
        inside=False,
        source="mqtt",
        usable=True,
    )
    assert state is False
    assert memory["value"] is False


def test_cloud_entry_is_immediate():
    state, memory = resolve_custom_area_presence(
        {},
        inside=True,
        source="private_cloud",
        usable=True,
        report_key=100,
    )
    assert state is True
    assert memory["outside_count"] == 0


def test_cloud_exit_requires_two_distinct_reports_after_active_area():
    state, memory = resolve_custom_area_presence(
        {},
        inside=True,
        source="mqtt",
        usable=True,
    )
    assert state is True

    state, memory = resolve_custom_area_presence(
        memory,
        inside=False,
        source="private_cloud",
        usable=True,
        report_key=101,
    )
    assert state is True
    assert memory["outside_count"] == 1

    # Re-reading the same coordinator snapshot must not count twice.
    state, memory = resolve_custom_area_presence(
        memory,
        inside=False,
        source="private_cloud",
        usable=True,
        report_key=101,
    )
    assert state is True
    assert memory["outside_count"] == 1

    state, memory = resolve_custom_area_presence(
        memory,
        inside=False,
        source="private_cloud",
        usable=True,
        report_key=102,
    )
    assert state is False
    assert memory["outside_count"] == 2


def test_stale_cloud_pose_does_not_publish_area_state():
    state, memory = resolve_custom_area_presence(
        {},
        inside=True,
        source="private_cloud",
        usable=False,
        report_key=100,
    )
    assert state is None
    assert memory == {}


def test_initial_fresh_cloud_outside_can_publish_off():
    state, memory = resolve_custom_area_presence(
        {},
        inside=False,
        source="private_cloud",
        usable=True,
        report_key=100,
    )
    assert state is False
    assert memory["value"] is False
