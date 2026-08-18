import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _position_fallback():
    spec = importlib.util.spec_from_file_location(
        "navimower_position_fallback_beta17", COMPONENT / "position_fallback.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_beta17_identity():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta17"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta17.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta17")


def test_docked_display_is_virtual_dock_even_from_outside_state():
    helper = _position_fallback().apply_docked_display_override
    result = {
        "current_physical_zone": "Outside mapped zones",
        "current_physical_zone_id": None,
        "current_physical_zone_source": "pose_unavailable",
        "current_physical_zone_stale": True,
    }
    assert helper(result, docked=True, pending_activity=None) is True
    assert result["current_physical_zone"] == "Dock"
    assert result["current_physical_zone_id"] is None
    assert result["current_physical_zone_source"] == "docked_state"
    assert result["current_physical_zone_position_source"] == "state"
    assert result["current_physical_zone_stale"] is False
    assert result["current_channel"] == "Not in channel"


def test_pending_activity_suppresses_stale_dock_override():
    helper = _position_fallback().apply_docked_display_override
    result = {"current_physical_zone": "Yard"}
    assert helper(result, docked=True, pending_activity="mowing") is False
    assert result["current_physical_zone"] == "Yard"


def test_24_hour_schedule_form_has_no_time_selectors():
    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    first = source.index("    async def async_step_navimower_schedule(\n")
    second = source.index("    async def async_step_navimower_schedule_window(\n", first)
    main_step = source[first:second]
    window_step = source[second:source.index("    async def async_step_gates(\n", second)]
    assert "TimeSelector()" not in main_step
    assert "SCHEDULE_MODE_CONTINUOUS" in main_step
    assert "TimeSelector()" in window_step


def test_scheduler_description_has_no_literal_backslash_newlines_or_none():
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    step = strings["options"]["step"]["navimower_schedule"]
    assert "\\n" not in step["description"]
    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    assert 'if not rows:\n            return ""' in source
    assert '"unavailable_note": self._schedule_unavailable_text()' in source


def test_navigation_fallback_uses_dock_override_before_pose_decoration():
    source = (COMPONENT / "navigation_fallback.py").read_text(encoding="utf-8")
    assert "apply_docked_display_override" in source
    assert 'docked=snapshot.get("docked") is True' in source
    assert 'state["position_source"] = "state"' in source
