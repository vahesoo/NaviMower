import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta14_identity():
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta14.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta14")


def test_manual_mowing_state_is_confirmed_work():
    source = (COMPONENT / "const.py").read_text(encoding="utf-8")
    assert 'STATE_MOWING_MANUAL: Final = "0212"' in source
    assert 'STATE_MOWING_MANUAL: ACTIVITY_MOWING' in source
    assert 'STATE_MOWING_MANUAL: "Manual mowing"' in source
    assert 'STATE_MOWING_MANUAL, STATE_RETURNING' in source


def test_local_completion_notification_is_not_duplicated():
    source = (COMPONENT / "notification_center.py").read_text(encoding="utf-8")
    assert '"NM1009"' not in source
    assert 'Vendor notifications already report completed one-time/scheduled tasks.' in source


def test_native_schedule_late_start_is_not_external():
    source = (COMPONENT / "notification_center.py").read_text(encoding="utf-8")
    assert 'def _schedule_period_containing_now' in source
    assert '"trigger": "schedule_window_observed_late"' in source
    assert 'active_schedule_period = self._schedule_period_containing_now(snapshot, now_local)' in source


def test_transient_private_endpoint_failures_are_quiet():
    source = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    assert '"consecutive_failures": 0' in source
    assert 'if consecutive in {3, 10, 25}:' in source
    assert 'transient failure %s; keeping last-good data' in source
    assert 'status["consecutive_failures"] = 0' in source


def test_discovery_scope_is_unchanged():
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    error_discovery = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    assert 'ERROR_DISCOVERY_TIMEOUT_SECONDS' in diagnostics
    assert 'MAX_PROBE_SECONDS = 24.0' in error_discovery
