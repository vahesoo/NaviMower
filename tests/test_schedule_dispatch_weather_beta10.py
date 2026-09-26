from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
SEMANTICS = COMPONENT / "schedule_dispatch_weather_semantics.py"
RUNTIME = COMPONENT / "runtime.py"
MANIFEST = COMPONENT / "manifest.json"


def _source() -> str:
    return SEMANTICS.read_text(encoding="utf-8")


def test_beta10_runtime_installs_final_dispatch_weather_layer() -> None:
    runtime = RUNTIME.read_text(encoding="utf-8")
    assert "from .schedule_dispatch_weather_semantics import install_schedule_dispatch_weather_semantics" in runtime
    queue_boundary = runtime.index("install_schedule_queue_boundary_semantics()")
    dispatch = runtime.index("install_schedule_dispatch_weather_semantics()")
    assert queue_boundary < dispatch


def test_beta10_scheduler_pending_commands_are_tagged_with_attempt_and_cycle_baseline() -> None:
    source = _source()
    assert 'pending["attempt"] = max(1, int(attempt))' in source
    assert '"baseline_vendor_start_time"' in source
    assert '"baseline_coverage_pct"' in source
    assert '"baseline_cycle_id"' in source


def test_beta10_unaccepted_scheduler_start_retries_but_never_retries_an_accepted_cycle_reset() -> None:
    source = _source()
    assert "_START_RETRY_SECONDS = 60.0" in source
    assert "_MAX_START_ATTEMPTS = 3" in source
    assert "if _zone_cycle_advanced(self, pending):" in source
    assert '"mow_start_accepted_but_not_running"' in source
    assert 'f"mow_retry:{zone_id}:attempt={attempt + 1}"' in source


def test_beta10_external_same_zone_command_is_adopted_not_relabelled_scheduler_confirmation() -> None:
    source = _source()
    assert "_external_trace_after(self, after=pending.get(\"sent_at\"))" in source
    assert '_trace_zone_ids(trace) == [zone_id]' in source
    assert 'runtime["ownership_source"] = str(trace.get("source") or "external_same_zone")' in source
    assert 'runtime["last_ownership_result"] = "external_same_zone_adopted"' in source


def test_beta10_external_other_zone_suspends_managed_queue() -> None:
    source = _source()
    assert 'runtime["suspended_reason"] = "external_override"' in source
    assert 'runtime["last_ownership_result"] = "external_other_zone_override"' in source


def test_beta10_weather_delay_uses_task_delay_and_vendor_150a_150f() -> None:
    source = _source()
    assert '"150A": "rain"' in source
    assert '"150F": "snow"' in source
    assert 'getter("task_delay")' in source
    assert 'runtime["resume_pending"] = True' in source
    assert 'runtime["last_ownership_result"] = "scheduler_start_delayed_by_weather"' in source


def test_beta10_weather_clear_waits_for_vendor_auto_resume_before_fallback_resume() -> None:
    source = _source()
    assert "_WEATHER_AUTO_RESUME_GRACE_SECONDS = 120.0" in source
    assert 'runtime["weather_clear_seen_at"] = _utc_now()' in source
    assert 'source="navimower_schedule_weather_resume"' in source
    assert 'continue_source="navimower_schedule_weather_continue_fallback"' in source


def test_beta10_weather_continue_is_non_resetting() -> None:
    source = _source()
    # The weather path delegates to the existing retained-task continuation path.
    # Its one-zone fallback uses reset=False; this final layer must never issue a
    # fresh reset command directly while recovering a delayed task.
    weather_block = source[source.index('if runtime.get("resume_pending") and interrupted in _WEATHER_REASONS:'):]
    assert "_continue_interrupted_task(" in weather_block
    assert "reset=True" not in weather_block


def test_beta10_or_newer_version_keeps_dispatch_weather_semantics() -> None:
    import json

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    version = str(manifest["version"])
    assert version == "0.4.5" or (version.startswith("0.4.5-beta") and int(version.rsplit("beta", 1)[1]) >= 10)
