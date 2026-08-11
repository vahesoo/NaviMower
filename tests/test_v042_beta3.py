"""Regression contracts for Navimower 0.4.2-beta3."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta3_manifest_release_notes_and_changelog() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.2-beta3"

    notes = (ROOT / ".github" / "release-notes" / "0.4.2-beta3.md").read_text()
    assert notes.startswith("title: Navimower 0.4.2-beta3")
    for phrase in (
        "navimower.resume",
        "c:behavior",
        "type 3",
        "vendor-retained",
        "docked",
        "charging",
        "field validation",
        "0.4.2-beta2",
    ):
        assert phrase in notes

    changelog = (ROOT / "CHANGELOG.md").read_text()
    # Beta3 remains historical after later cumulative betas prepend their own
    # changelog sections; it must not require beta3 to stay the newest entry.
    assert "## 0.4.2-beta3" in changelog
    assert "navimower.resume" in changelog


def test_beta3_resume_helper_does_not_create_a_new_mow_task() -> None:
    source = (COMPONENT / "resume.py").read_text()
    ast.parse(source)

    assert "async def async_resume_task" in source
    assert "coordinator.client.resume" in source
    assert '"c:behavior type=3"' in source
    assert '"zones_sent": False' in source
    assert '"progress_reset_requested": False' in source
    assert "set_pending_activity(ACTIVITY_MOWING)" in source
    assert "clear_pending_activity()" in source

    # Resume must stay a separate vendor command. It must not silently become a
    # selected-zone start or manufacture a new Navimower cycle/history reset.
    assert "mow_zones" not in source
    assert "start_new_mowing_cycle" not in source
    assert "encode_partition_ids" not in source
    assert "mow_setup" not in source


def test_beta3_exposes_resume_as_a_separate_home_assistant_action() -> None:
    source = (COMPONENT / "services.py").read_text()
    yaml_source = (COMPONENT / "services.yaml").read_text()
    ast.parse(source)

    assert 'SERVICE_RESUME = "resume"' in source
    assert "RESUME_SCHEMA" in source
    assert "async_resume_task" in source
    assert 'source="navimower.resume"' in source
    assert "if not hass.services.has_service(DOMAIN, SERVICE_RESUME)" in source
    assert "SERVICE_RESUME," in source

    assert "resume:" in yaml_source
    assert "Resume interrupted mowing" in yaml_source
    assert "does not select zones" in yaml_source
    assert "docked/charging" in yaml_source


def test_beta3_paused_lawn_start_reuses_the_same_resume_helper() -> None:
    source = (COMPONENT / "lawn_mower.py").read_text()
    ast.parse(source)

    assert "from .resume import async_resume_task" in source
    assert 'if self.data.get("state_code") == STATE_PAUSED:' in source
    assert "await async_resume_task(" in source
    assert 'source="lawn_mower.start_mowing_paused"' in source

    # Beta3 deliberately does not auto-resume every docked/charging Start yet.
    assert "partition_setup = mow_setup(reset=True, ordered=ordered)" in source
    assert 'source="lawn_mower.start_mowing_reset"' in source


def test_beta3_download_diagnostics_contains_only_cached_resume_trace() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    resume = (COMPONENT / "resume.py").read_text()
    ast.parse(diagnostics)
    ast.parse(resume)

    assert "from .resume import resume_command_diagnostics" in diagnostics
    assert '"last_resume_command": sanitize(resume_command_diagnostics(coordinator))' in diagnostics
    assert "downloading diagnostics never sends Resume" in diagnostics
    assert "async_resume_task(" not in diagnostics

    for field in (
        '"state_code_before"',
        '"activity_before"',
        '"docked_before"',
        '"task_progress_before"',
        '"task_progress_source_before"',
        '"task_mowed_area_before"',
        '"request_accepted"',
        '"command_number"',
    ):
        assert field in resume
