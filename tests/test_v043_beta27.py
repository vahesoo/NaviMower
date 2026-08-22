import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _function(source: str, name: str) -> str:
    start = source.index(f"    async def {name}(")
    next_method = source.find("\n    async def ", start + 1)
    if next_method < 0:
        next_method = source.find("\n    def ", start + 1)
    return source[start:] if next_method < 0 else source[start:next_method]


def test_beta27_identity_and_release_notes():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta27"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta27.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta27")


def test_scheduler_new_zone_start_stays_pending_until_vendor_confirmation():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    ast.parse(source)
    send = _function(source, "_async_send_mow")
    confirm = _function(source, "_confirm_pending")

    assert '"kind": "mow" if reset else "continue"' in send
    assert '"baseline_completed_at": row.get("last_completed_at") if reset else None' in send
    assert '"vendor_mowing_at_send": vendor_mowing_at_send if reset else None' in send
    assert "self.coordinator.start_new_mowing_cycle" not in send
    assert 'if not reset:' in send

    assert 'if kind == "mow":' in confirm
    assert "self._pending_mow_confirmed(pending, data)" in confirm
    assert "self.coordinator.start_new_mowing_cycle([zone_id], source=source)" in confirm
    assert 'self._runtime["active_zone_id"] = zone_id' in confirm


def test_scheduler_uses_raw_vendor_state_not_optimistic_activity_for_new_start():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    assert "MQTT_STATE_MOWING" in source
    assert "STATE_MOWING" in source
    assert "STATE_MOWING_MANUAL" in source
    assert 'data.get("mqtt_vehicle_state")' in source
    assert 'data.get("state_code")' in source

    confirmed_start = source[source.index("    def _pending_mow_confirmed"):source.index("    def _sync_active_cycle_id")]
    assert "ACTIVITY_MOWING" not in confirmed_start
    assert 'data.get("active_zone_progress_zone_id")' in confirmed_start


def test_scheduler_does_not_dispatch_a_new_zone_while_vendor_reports_charging():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    assert "MQTT_STATE_CHARGING" in source
    assert "STATE_IDLE_DOCKED_POST" in source
    evaluate = _function(source, "_evaluate_locked")
    guard = evaluate.index("if self._vendor_charging(data):")
    dispatch = evaluate.index("await self._async_send_mow(zone_id, reset=True")
    assert guard < dispatch


def test_unconfirmed_start_cannot_leave_a_fake_active_zone():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    reconcile = _function(source, "_reconcile_unconfirmed_mow_start")
    assert 'self._runtime["pending_command"] = None' in reconcile
    assert 'self._runtime["suspended_reason"] = "mow_start_not_confirmed"' in reconcile
    assert 'self._runtime["active_zone_id"] =' not in reconcile
