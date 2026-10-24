"""Behavior tests for bounded beta28 command confirmation and refresh."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "navimower"


def _load_outcomes():
    package = sys.modules.setdefault("navimower_beta28", types.ModuleType("navimower_beta28"))
    package.__path__ = [str(ROOT)]
    writer_name = "navimower_beta28.setting_write"
    if writer_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(writer_name, ROOT / "setting_write.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[writer_name] = module
        spec.loader.exec_module(module)
    name = "navimower_beta28.control_command_outcomes"
    spec = importlib.util.spec_from_file_location(name, ROOT / "control_command_outcomes.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _owner(responses, *, mqtt=4, cloud="0101"):
    class Hass:
        async def async_add_executor_job(self, func, *args):
            return func(*args)
        def async_create_task(self, coro, *_args):
            return asyncio.create_task(coro)

    calls = []
    refreshes = []
    statuses = {"index2": {"last_attempt_mono": 1, "last_success_mono": 1}}
    def status(_serial, _number):
        calls.append(1)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    async def refresh():
        refreshes.append(1)
        statuses["index2"]["last_success_mono"] = time.monotonic()
        if cloud:
            owner._raw_cache["index2"]["vehicle_state"] = cloud

    owner = SimpleNamespace(
        hass=Hass(), entry=SimpleNamespace(entry_id="test"), sn="fake",
        client=SimpleNamespace(command_status=status),
        _endpoint_status=statuses, _raw_cache={"index2": {"vehicle_state": "0101"}},
        _mqtt_state_last_update=None, _mqtt_named_state_last_update=None,
        _mqtt_named_state=None, _fresh_mqtt_vehicle_state=lambda: mqtt,
        _fresh_mqtt_position=lambda: {"x": 1, "y": 1},
        async_request_refresh=refresh,
        _shutdown_complete=False,
    )
    return owner, calls, refreshes


def _run(coro):
    return asyncio.run(coro)


def test_history_initializes_under_internal_recent_key():
    module = _load_outcomes()
    owner, _, _ = _owner([{"status": "3"}])
    module.record_control_command_error(owner, SimpleNamespace(__name__="dock"), RuntimeError())
    snapshot = module.control_command_snapshot(owner)
    assert snapshot["latest"]["command"] == "dock"
    assert snapshot["latest"]["outcome"] == "send_error"
    assert "recent" in owner._control_command_outcome_state
    assert "recent_commands" not in owner._control_command_outcome_state


def test_mqtt_observation_requires_post_command_receipt():
    module = _load_outcomes()
    owner, _, _ = _owner([{"status": "3"}])
    row = module._new_row(owner, "mow_zones", acknowledged=True, has_number=True)
    baseline = time.monotonic()
    owner._mqtt_state_last_update = baseline - 1
    assert not module._confirm_observed_state(owner, row, baseline)
    owner._mqtt_state_last_update = baseline + 0.2
    assert module._confirm_observed_state(owner, row, baseline)
    assert row["physical_source"] == "mqtt_vehicle_state"


def test_pause_cannot_confirm_ambiguous_mqtt_3():
    module = _load_outcomes()
    owner, _, _ = _owner([{"status": "3"}], mqtt=3)
    row = module._new_row(owner, "pause", acknowledged=True, has_number=True)
    baseline = time.monotonic()
    owner._mqtt_state_last_update = baseline + 1
    assert not module._confirm_observed_state(owner, row, baseline)
    owner._mqtt_named_state = "isPaused"
    owner._mqtt_named_state_last_update = baseline + 1
    assert module._confirm_observed_state(owner, row, baseline)


def test_control_monitor_targeted_refresh_and_no_command_replay(monkeypatch):
    module = _load_outcomes()
    async def check():
        owner, calls, refreshes = _owner([{"status": "2"}, {"status": "3"}], mqtt=None, cloud="0210")
        monkeypatch.setattr(module, "CONTROL_COMMAND_STATUS_POLL_AT_SECONDS", (0.0, 0.0))
        monkeypatch.setattr(module, "CONTROL_COMMAND_REFRESH_AT_SECONDS", frozenset({0.0}))
        def mow_zones():
            raise AssertionError("must never resend")
        module.record_control_command_send(owner, mow_zones, {"cmd_num": "77"})
        await next(iter(owner._control_command_outcome_tasks.values()))
        row = module.control_command_snapshot(owner)["latest"]
        assert row["outcome"] == "answered"
        assert row["physical_confirmation"] == "state_observed"
        assert row["physical_source"] == "private_index2"
        assert row["state_refresh_endpoints"] == ["index2"]
        assert len(refreshes) == 1
        assert len(calls) == 2
    _run(check())


def test_confirmed_mqtt_state_skips_refresh(monkeypatch):
    module = _load_outcomes()
    async def check():
        owner, _, refreshes = _owner([{"status": "3"}], mqtt=4)
        start = time.monotonic()
        owner._mqtt_state_last_update = start + 1
        monkeypatch.setattr(module, "CONTROL_COMMAND_STATUS_POLL_AT_SECONDS", (0.0,))
        monkeypatch.setattr(module, "CONTROL_COMMAND_REFRESH_AT_SECONDS", frozenset({0.0}))
        def mow_zones():
            pass
        module.record_control_command_send(owner, mow_zones, {"cmd_num": "77"})
        await next(iter(owner._control_command_outcome_tasks.values()))
        row = module.control_command_snapshot(owner)["latest"]
        assert row["physical_confirmation"] == "state_observed"
        assert refreshes == []
    _run(check())


def test_send_is_not_blocked_by_control_refresh():
    source = (ROOT / "coordinator.py").read_text(encoding="utf-8")
    send = source[source.index("    async def async_send("):]
    assert "record_control_command_send" in send
    assert "except Exception:" in send or "except Exception:  # noqa" in send
    assert 'not in {\n            "mow_zones", "pause", "dock", "resume",' in send
    assert "await self.async_request_refresh()" in send
