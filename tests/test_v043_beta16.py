import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _schedule_logic():
    spec = importlib.util.spec_from_file_location(
        "navimower_schedule_logic_beta16", COMPONENT / "schedule_logic.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_beta16_identity():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta16"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta16.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta16")


def test_uncompleted_zone_is_not_eligible_or_ranked_first():
    logic = _schedule_logic()
    zones = [
        {"id": 1, "name": "Never completed", "last_completed_at": None},
        {"id": 2, "name": "Proven", "last_completed_at": "2026-08-17T10:00:00+00:00"},
    ]
    eligible = logic.filter_schedule_zones(zones, [1, 2])
    assert [row["id"] for row in eligible] == [2]
    assert logic.select_oldest_zone(zones)["id"] == 2


def test_schedule_time_parser_accepts_time_selector_seconds():
    logic = _schedule_logic()
    assert logic.format_hhmm(logic.parse_hhmm("09:30:00", "10:00")) == "09:30"


def test_options_flow_exposes_multi_zone_and_24_hour_configuration():
    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    assert 'menu_options=["general", "navimower_schedule", "gates", "channels"]' in source
    assert "multiple=True" in source
    assert '"24 hours"' in source
    assert '"unavailable_note": self._schedule_unavailable_text()' in source
    step = strings["options"]["step"]["navimower_schedule"]
    assert step["data"]["navimower_schedule_zone_ids"] == "Automatic mowing zones"
    assert "fully completed manually once" in step["description"]


def test_scheduler_filters_allowlist_and_has_continuous_rounds():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    assert "def _eligible_zones" in source
    assert "filter_schedule_zones(" in source
    assert 'return True, "continuous"' in source
    assert "eligible_ids.issubset(completed)" in source
    assert 'self._runtime["round_index"]' in source
    assert "Configure at least one successfully completed automatic mowing zone first" in source


def test_existing_enabled_beta_scheduler_gets_one_time_explicit_allowlist_migration():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    assert "_legacy_selection_migration_allowed" in source
    assert "def _maybe_migrate_legacy_zone_selection" in source
    assert "OPT_SCHEDULE_ZONE_IDS: [str(value) for value in sorted(proven)]" in source
    assert "future zones" in source
