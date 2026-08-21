import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _source(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


def test_beta24_identity_and_release_notes():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta24"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta24.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta24")


def test_schedule_entities_require_saved_setup_and_cleanup_old_registry_rows():
    schedule = _source("navimower_schedule.py")
    switch = _source("switch.py")
    time_source = _source("time.py")
    for source in (schedule, switch, time_source):
        ast.parse(source)

    assert "def configured(self) -> bool:" in schedule
    assert "return self._selection_configured" in schedule
    assert "controller is not None and controller.configured" in switch
    assert 'f"{coordinator.sn}_navimower_schedule"' in switch
    assert "registry.async_remove(entity_id)" in switch
    assert "controller is None or not controller.configured" in time_source
    assert 'f"{coordinator.sn}_navimower_schedule_{key}"' in time_source


def test_scheduler_cycle_id_waits_for_the_real_new_history_session():
    source = _source("navimower_schedule.py")
    ast.parse(source)
    assert "def _sync_active_cycle_id(self) -> bool:" in source
    assert 'active.get("cycle_reset_zone_ids")' in source
    assert 'self._runtime["active_cycle_id"] = str(active["id"])' in source
    assert "if self._sync_active_cycle_id():" in source

    send_start = source.index("    async def _async_send_mow")
    send_end = source.index("    async def _async_send_dock", send_start)
    send = source[send_start:send_end]
    assert 'self._runtime["active_cycle_id"] = None' in send
    assert 'post_reset_row.get("cycle_id")' not in send


def test_observed_mowing_start_is_neutral_when_this_ha_has_no_command_trace():
    source = _source("notification_center.py")
    ast.parse(source)
    start = source.index("        ids = self._observed_task_zone_ids(snapshot)")
    end = source.index("    def _handle_mowing_stop", start)
    fallback = source[start:end]
    assert '"NM1003"' in fallback
    assert '"Mowing task started"' in fallback
    assert 'kind="observed_mowing_started"' in fallback
    assert 'confidence="observed_start_without_local_command"' in fallback
    assert '"origin": "observed"' in fallback
    assert '"trigger": "observed_without_local_command"' in fallback
    assert '_emit(\n            "NM1003",\n            "External mowing task started"' not in fallback


def test_low_battery_pause_uses_vendor_row_but_retains_resume_context():
    source = _source("notification_center.py")
    ast.parse(source)
    start = source.index('        threshold = _as_float((snapshot.get("settings") or {}).get("return_battery_level"))')
    end = source.index('        self._interrupted_reason = "unknown"', start)
    charging = source[start:end]
    assert 'self._interrupted_reason = "charging"' in charging
    assert '"charging_paused_at"' in charging
    assert '"inferred_from_return_battery_threshold"' in charging
    assert 'return False' in charging
    assert '_emit(' not in charging
    assert '"NM1008"' in source
    assert '"Mowing resumed after charging"' in source


def test_readme_is_current_state_documentation_with_installation_before_features():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.index("## Installation") < readme.index("## Main features")
    assert readme.index("## Setup flow") < readme.index("## Main features")
    assert "## Navimower Schedule" in readme
    assert "Before this setup is saved" in readme
    assert "## 0.4.2 beta development" not in readme
    assert "## Upgrade from 0.4.0 to 0.4.1" not in readme
    assert "### v0.3.4" not in readme
    assert "External mowing task started" not in readme
    assert "low-battery return uses the vendor Device notification" in readme
    assert "[CHANGELOG.md](CHANGELOG.md)" in readme
