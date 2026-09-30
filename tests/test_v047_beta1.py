"""Release contract for Navimower 0.4.7-beta1."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta1_version_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.7-beta1"
    notes = (ROOT / ".github" / "release-notes" / "0.4.7-beta1.md").read_text(
        encoding="utf-8"
    )
    assert notes.startswith("title: Navimower 0.4.7-beta1\n")
    for marker in (
        "Schedule zone-handoff confirmation",
        "mow_start_zone_mismatch",
        "navimower.export_raw_data",
        "version-driven publishing",
    ):
        assert marker in notes


def test_beta1_handoff_mismatch_waits_for_requested_zone_evidence() -> None:
    logic = (COMPONENT / "schedule_logic.py").read_text(encoding="utf-8")
    schedule = (COMPONENT / "navimower_schedule.py").read_text(encoding="utf-8")
    assert "handoff_at_send" in logic
    assert '"handoff_conflict": True' in logic
    assert "if matching:" in logic
    assert 'data_before_send.get("activity") in {' in schedule
    assert '"handoff_at_send": handoff_at_send if reset else None' in schedule


def test_beta1_prerelease_raw_export_is_explicit_and_bounded() -> None:
    services = (COMPONENT / "services.py").read_text(encoding="utf-8")
    yaml = (COMPONENT / "services.yaml").read_text(encoding="utf-8")
    raw = (COMPONENT / "raw_export.py").read_text(encoding="utf-8")
    assert 'SERVICE_EXPORT_RAW_DATA = "export_raw_data"' in services
    assert "async_export_raw_data" in services
    assert "export_raw_data:" in yaml
    assert '"private_cloud_fresh"' in raw
    assert '"map_geometry_decoded"' in raw
    assert '"mqtt_parsed_cache"' in raw
    assert "sanitize(" not in raw
    assert not (COMPONENT / "raw_mqtt_semantics.py").exists()


def test_beta1_release_is_version_bump_driven_not_issue_driven() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "issue-publish-prerelease.yml"
    ).read_text(encoding="utf-8")
    active = "\n".join(
        line for line in workflow.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "push:" in active
    assert "custom_components/navimower/manifest.json" in active
    assert "workflow_dispatch:" in active
    assert "issues:" not in active
    assert "github.event.issue" not in active
    assert 'if [[ "${VERSION}" == *-* ]]' in active
    assert 'gh release create "${TAG}"' in active
