from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta19_release_notes_remain_available():
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta19.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta19")


def test_24_hour_mode_leaves_night_mowing_to_the_mower():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    start = source.index("    def _window_state(self, now: datetime)")
    end = source.index("    def _zone(self, zone_id", start)
    resolver = source[start:end]
    assert 'return True, "continuous"' in resolver
    assert "night_mow" not in source
    assert "sunrise" not in resolver.lower()
    assert "sunset" not in resolver.lower()


def test_closed_window_is_a_hard_outer_gate_even_when_scheduler_is_suspended():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    evaluate = source[source.index("    async def _evaluate_locked"):]
    closed = evaluate.index("        if not in_window:")
    suspended = evaluate.index('        if self._runtime.get("suspended_reason"):', closed)
    assert closed < suspended
    enforce_start = source.index("    async def _enforce_closed_window")
    enforce_end = source.index("    async def _continue_interrupted_task", enforce_start)
    enforce = source[enforce_start:enforce_end]
    assert "ACTIVITY_MOWING, ACTIVITY_PAUSED" in enforce
    assert 'await self._async_send_dock("navimower_schedule_window_closed")' in enforce
    assert 'self._runtime["resume_pending"] = True' in enforce
    assert 'self._runtime["progress_before_interrupt"]' in enforce


def test_restart_restores_runtime_and_reconciles_stale_new_zone_start():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    assert "restored.update" in source
    assert "self._runtime = restored" in source
    assert "self._queue_evaluation()" in source
    assert "_MOW_CONFIRM_SECONDS = 120" in source
    assert "_reconcile_unconfirmed_mow_start" in source
    assert '"mow_start_not_confirmed"' in source
    assert "automatic reset retry was refused" in source
    assert '"late_mow_confirmed:' in source


def test_restart_reconciliation_runs_before_window_and_suspend_guards():
    source = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    evaluate_start = source.index("    async def _evaluate_locked")
    evaluate_end = source.index("    async def _confirm_active_completion", evaluate_start)
    evaluate = source[evaluate_start:evaluate_end]
    reconcile = evaluate.index("await self._reconcile_unconfirmed_mow_start(activity)")
    closed = evaluate.index("if not in_window:")
    suspended = evaluate.index('if self._runtime.get("suspended_reason"):')
    assert reconcile < closed < suspended
    assert "reset=True" not in source[source.index("    async def _reconcile_unconfirmed_mow_start"):source.index("    def _retry_ready")]
