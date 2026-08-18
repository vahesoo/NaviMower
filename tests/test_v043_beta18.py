import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta18_identity():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta18"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta18.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta18")


def test_completion_is_current_cycle_confirmed():
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    assert '"vendor_completed_at": (' in coordinator
    assert '"last_completed_at": None' in coordinator
    assert '"task_zone_seen_incomplete"' in history
    assert '"task_zone_completion_confirmed"' in history
    assert "_async_repair_unverified_zone_completions" in history
    start = history.index("    def prepare_cycle(\n")
    end = history.index("    def cycle_diagnostics", start)
    assert '"last_completed_at"' not in history[start:end]
