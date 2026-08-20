import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta23_identity_and_notes():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta23"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta23.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta23")


def test_cycle_detector_uses_vendor_zone_coverage_only():
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    start = history.index("    def _progress_rows")
    end = history.index("    def start_new_cycle", start)
    resolver = history[start:end]
    assert 'progress = _as_int(coverage.get("pct"))' in resolver
    assert 'detail.get("progress")' not in resolver
    assert 'detail.get("percentage")' not in resolver


def test_live_progress_regression_guard():
    history = (COMPONENT / "history.py").read_text(encoding="utf-8")
    start = history.index("    def update_zone_history")
    end = history.index("    def update_from_snapshot", start)
    update = history[start:end]
    assert "previous_live_progress - live_progress >= 10" in update
    assert "vendor_live_progress >= previous_live_progress - 3" in update
    assert "vendor_age" in update
    assert "if not corroborated_regression:" in update
    assert '"private_map_work_position"' not in update


def test_readme_documents_navimower_schedule():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Navimower Schedule" in readme
    assert "Time window" in readme
    assert "24 hours" in readme
    assert "Night mowing" in readme
    assert "Rain delay" in readme
    assert "Last completed" in readme
