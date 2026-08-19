from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

ROOT = Path.cwd()
COMPONENT = ROOT / "custom_components" / "navimower"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one marker, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).lstrip(), encoding="utf-8")


# Identity.
manifest_path = COMPONENT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != "0.4.3-beta18":
    raise SystemExit(f"Expected beta18 base, got {manifest.get('version')!r}")
manifest["version"] = "0.4.3-beta19"
manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# Scheduler restart hardening: a reset=true start command must never block the
# controller forever after Home Assistant restarts. If the mower does not
# confirm Mowing in time, suspend rather than automatically repeating reset.
schedule_path = COMPONENT / "navimower_schedule.py"
replace_once(
    schedule_path,
    '''_RESUME_CONFIRM_SECONDS = 90\n_CONTINUE_CONFIRM_SECONDS = 120\n_DOCK_RETRY_SECONDS = 60\n''',
    '''_RESUME_CONFIRM_SECONDS = 90\n_CONTINUE_CONFIRM_SECONDS = 120\n_MOW_CONFIRM_SECONDS = 120\n_DOCK_RETRY_SECONDS = 60\n''',
    "mow confirmation timeout",
)
replace_once(
    schedule_path,
    '''    def _pending_age(self) -> float | None:\n        pending = self._runtime.get("pending_command")\n        return _age_seconds(pending.get("sent_at")) if isinstance(pending, dict) else None\n\n    def _retry_ready(self) -> bool:\n''',
    '''    def _pending_age(self) -> float | None:\n        pending = self._runtime.get("pending_command")\n        return _age_seconds(pending.get("sent_at")) if isinstance(pending, dict) else None\n\n    async def _reconcile_unconfirmed_mow_start(self, activity: Any) -> None:\n        """Recover safely when a new-zone start confirmation is lost across restart."""\n        pending = self._runtime.get("pending_command")\n        if isinstance(pending, dict) and pending.get("kind") == "mow":\n            age = self._pending_age()\n            if age is not None and age >= _MOW_CONFIRM_SECONDS and activity != ACTIVITY_MOWING:\n                zone_id = pending.get("zone_id") or self._runtime.get("active_zone_id")\n                self._runtime["pending_command"] = None\n                self._runtime["suspended_reason"] = "mow_start_not_confirmed"\n                self._runtime["last_error"] = (\n                    "New-zone mowing start was not confirmed; automatic reset retry was refused"\n                )\n                self._runtime["last_command"] = f"mow_start_unconfirmed:{zone_id}"\n                self._runtime["last_command_at"] = _utc_now()\n                await self._save()\n                return\n\n        if (\n            self._runtime.get("suspended_reason") == "mow_start_not_confirmed"\n            and activity == ACTIVITY_MOWING\n            and self._runtime.get("active_zone_id") is not None\n        ):\n            zone_id = self._runtime.get("active_zone_id")\n            self._runtime["suspended_reason"] = None\n            self._runtime["last_error"] = None\n            self._runtime["last_command"] = f"late_mow_confirmed:{zone_id}"\n            self._runtime["last_command_at"] = _utc_now()\n            await self._save()\n\n    def _retry_ready(self) -> bool:\n''',
    "stale mow reconciliation",
)
replace_once(
    schedule_path,
    '''        completed_now = await self._confirm_active_completion()\n        activity = data.get("activity")\n        await self._confirm_pending(activity)\n\n        if not in_window:\n''',
    '''        completed_now = await self._confirm_active_completion()\n        activity = data.get("activity")\n        await self._confirm_pending(activity)\n        await self._reconcile_unconfirmed_mow_start(activity)\n\n        if not in_window:\n''',
    "scheduler reconciliation hook",
)

# Beta18 remains a historical regression suite after beta19 takes identity.
beta18_test = ROOT / "tests" / "test_v043_beta18.py"
replace_once(
    beta18_test,
    '''def test_beta18_identity():\n    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))\n    assert manifest["version"] == "0.4.3-beta18"\n    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta18.md").read_text(encoding="utf-8")\n    assert notes.startswith("title: Navimower 0.4.3-beta18")\n''',
    '''def test_beta18_release_notes_remain_available():\n    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta18.md").read_text(encoding="utf-8")\n    assert notes.startswith("title: Navimower 0.4.3-beta18")\n''',
    "beta18 historical identity",
)
replace_once(
    beta18_test,
    "import json\n",
    "",
    "remove unused beta18 json import",
)

write(ROOT / "tests" / "test_v043_beta19.py", r'''
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta19_identity():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta19"
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
''')

changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text(encoding="utf-8")
marker = "# Changelog\n\n\n"
if not changelog.startswith(marker):
    raise SystemExit("Unexpected changelog header")
section = '''## 0.4.3-beta19\n\nNavimower Schedule window/restart hardening.\n\n### Fixed\n\n- Prevent a stale unconfirmed new-zone `mow` command from blocking Navimower Schedule forever after Home Assistant restarts or loses the immediate state transition.\n- After 120 seconds without a confirmed Mowing state, clear the stale pending command and suspend safely instead of automatically repeating a `reset=true` start.\n- Recover automatically if the mower later confirms that same active-zone mowing start.\n\n### Safety\n\n- Keep Time window as the hard outer gate: an observed Mowing/Paused state outside the window is sent Dock/Home while the interrupted zone and progress are retained for resume.\n- Keep 24 hours mode independent from the mower's Night mowing setting; sunset/sunrise pauses remain the mower/user setting's responsibility.\n- A Home Assistant restart during already-confirmed mowing restores scheduler runtime without starting a new mowing cycle.\n\n\n'''
changelog_path.write_text(marker + section + changelog[len(marker):], encoding="utf-8")

write(ROOT / ".github" / "release-notes" / "0.4.3-beta19.md", r'''
title: Navimower 0.4.3-beta19

Navimower Schedule window/restart hardening.

### Fixed
- A stale unconfirmed new-zone start can no longer block **Navimower Schedule** indefinitely after a Home Assistant restart or a lost immediate state transition.
- If a new-zone `mow` command is still unconfirmed after **120 seconds**, Navimower clears that stale pending command and suspends the scheduler instead of automatically repeating a `reset=true` start.
- If the mower later reports Mowing for the retained active zone, the scheduler clears that safety suspension and resumes normal tracking automatically.

### Safety
- **Time window** remains the hard outer gate. If the mower is observed Mowing or Paused outside the configured window, Navimower sends Dock/Home and retains the interrupted zone/progress for the next allowed resume.
- **24 hours** mode deliberately does not inspect or override **Night mowing**. With Night mowing off, the mower may pause around sunset and resume according to its own retained-task/daylight logic; with it on, mowing can continue overnight.
- Home Assistant restart restores the saved scheduler runtime. Already-confirmed active mowing is not restarted and no new cycle is created just because Home Assistant restarted.

### Unchanged
- Low-battery charging, sunset/night pauses and other mower-internal pauses remain controlled by the mower while the Navimower window is open.
- Interrupted-task recovery still prefers the vendor Resume command and then `mow(reset=false)`; automatic reset fallback remains refused.
''')
