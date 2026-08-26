"""Regression guards for Navimower 0.4.3-beta55 schedule pause semantics."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_schedule_switch_is_pause_resume_not_runtime_reset() -> None:
    source = (COMPONENT / "schedule_pause_semantics.py").read_text(encoding="utf-8")
    assert 'self._runtime = self._empty_runtime()' not in source[source.index("async def _async_set_enabled"):source.index("async def _async_reset_schedule")]
    assert '"schedule_paused"' in source
    assert '_adopt_retained_task(self)' in source
    assert 'await self.async_evaluate()' in source


def test_reset_schedule_is_explicit_and_command_free() -> None:
    source = (COMPONENT / "schedule_pause_semantics.py").read_text(encoding="utf-8")
    reset_block = source[source.index("async def _async_reset_schedule"):source.index("def _resolve_controller")]
    assert 'self._runtime = self._empty_runtime()' in reset_block
    assert '_async_send_mow' not in reset_block
    assert '_async_send_dock' not in reset_block
    assert '_continue_interrupted_task' not in reset_block
    assert 'Reset schedule is refused while the mower is mowing or returning' in reset_block


def test_retained_task_adoption_keeps_continue_semantics() -> None:
    source = (COMPONENT / "schedule_pause_semantics.py").read_text(encoding="utf-8")
    adopt_block = source[source.index("def _adopt_retained_task"):source.index("async def _async_set_enabled")]
    assert 'resume_pending' in adopt_block
    assert 'interrupted_zone_id' in adopt_block
    assert 'start_new_mowing_cycle' not in adopt_block
    assert 'reset=True' not in adopt_block


def test_reset_action_is_documented_and_runtime_extension_installed() -> None:
    services = (COMPONENT / "services.yaml").read_text(encoding="utf-8")
    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    assert "\nreset_schedule:\n" in services
    assert "install_schedule_pause_semantics" in runtime
