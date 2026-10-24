"""Beta29: privacy-safe control tracing and bounded pending-command follow-up."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from test_control_command_confirmation import _load_outcomes, _owner

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "navimower"


def test_control_context_records_mow_intent_but_not_vendor_payload():
    module = _load_outcomes()
    owner, _, _ = _owner([{"status": "2"}], mqtt=None)
    owner._last_mow_command_trace = {
        "source": "navimower.mow", "started_at_utc": "2026-10-24T08:00:00+00:00",
        "resolved_zone_ids": [43], "reset": False, "ordered": True,
        "partition_setup": 18,
        "state_before": {"state_code": "0211", "activity": "paused"},
    }
    def mow_zones():
        pass
    context = module.capture_control_command_context(
        owner, mow_zones, ("SERIAL", "2b00", 18),
    )
    assert context["command_source"] == "navimower.mow"
    assert context["request"] == {
        "command_family": "mow_zones", "zone_ids": [43],
        "reset": False, "ordered": True, "partition_setup": 18,
    }
    assert context["state_before"]["state_code"] == "0211"
    row = module._new_row(owner, "mow_zones", acknowledged=True, has_number=True, context=context)
    snapshot = module.control_command_snapshot(owner)["latest"]
    assert snapshot["request"]["zone_ids"] == [43]
    assert snapshot["command_source"] == "navimower.mow"
    assert "_trace_token" not in snapshot
    assert "SERIAL" not in str(snapshot)
    assert "2b00" not in str(snapshot)
    assert "cmd_num" not in str(snapshot)


def test_sixty_second_sent_poll_preserves_early_refresh_budget(monkeypatch):
    module = _load_outcomes()
    assert module.CONTROL_COMMAND_STATUS_POLL_AT_SECONDS[-1] == 60.0
    assert module.CONTROL_COMMAND_REFRESH_AT_SECONDS == frozenset({4.0, 15.0})
    async def scenario():
        owner, calls, refreshes = _owner([{"status": "2"}], mqtt=None, cloud=None)
        monkeypatch.setattr(module, "CONTROL_COMMAND_STATUS_POLL_AT_SECONDS", (0.0, 0.0, 0.0))
        monkeypatch.setattr(module, "CONTROL_COMMAND_REFRESH_AT_SECONDS", frozenset({0.0}))
        def pause():
            pass
        module.record_control_command_send(owner, pause, {"cmd_num": "123"})
        task = next(iter(owner._control_command_outcome_tasks.values()))
        await task
        row = module.control_command_snapshot(owner)["latest"]
        assert len(calls) == 3
        assert row["outcome"] == "poll_timeout"
        assert row["physical_confirmation"] == "unconfirmed"
        assert row["state_refresh_count"] == 3
        assert len(refreshes) == 3
    asyncio.run(scenario())


def test_resume_diagnostics_and_control_trace_are_explicitly_allowlisted():
    source = (ROOT / "diagnostics.py").read_text(encoding="utf-8")
    assert '"last_resume_command": _safe_resume_command_trace(coordinator)' in source
    assert '"last_mow_command": _safe_mow_command_trace(coordinator)' in source
    assert '"command_number"' not in source.split("def _safe_resume_command_trace(", 1)[1].split("def _safe_mow_command_trace(", 1)[0]
    assert '"send_response"' not in source.split("def _safe_mow_command_trace(", 1)[1].split("def _selected(", 1)[0]
    coordinator = (ROOT / "coordinator.py").read_text(encoding="utf-8")
    assert 'record_control_command_send(self, func, result, context=context)' in coordinator
    assert 'record_control_command_verification(' not in coordinator
    assert 'def record_mow_command_verification(' in coordinator
