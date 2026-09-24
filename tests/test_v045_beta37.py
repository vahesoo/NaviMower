"""Regression contracts for Navimower 0.4.5-beta37 smart task continuation."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta37_release_metadata() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    assert version.startswith("0.4.5-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 37
    notes = (ROOT / ".github" / "release-notes" / "0.4.5-beta37.md").read_text(
        encoding="utf-8"
    )
    assert notes.startswith("title: Navimower 0.4.5-beta37\n")
    for phrase in (
        "Backend-owned smart Resume",
        "navimower.continue_task",
        "Returning",
        "Error",
        "confirmed completed",
        "Map Card",
    ):
        assert phrase in notes


def test_beta37_registers_smart_continue_action() -> None:
    services = (COMPONENT / "services.py").read_text(encoding="utf-8")
    yaml = (COMPONENT / "services.yaml").read_text(encoding="utf-8")

    assert 'SERVICE_CONTINUE_TASK = "continue_task"' in services
    assert "CONTINUE_TASK_SCHEMA = DEVICE_ONLY_SCHEMA" in services
    assert "async def _continue_task(" in services
    assert "task_resume_decision(" in services
    assert "RESUME_STRATEGY_ORDERED_RUN" in services
    assert "RESUME_STRATEGY_VENDOR" in services
    assert 'source="navimower.continue_task"' in services
    assert "(SERVICE_CONTINUE_TASK, _continue_task, CONTINUE_TASK_SCHEMA)" in services
    assert "continue_task:" in yaml
    assert "safest backend-owned" in yaml
    assert "reset=false" in yaml


def test_beta37_publishes_resume_contract() -> None:
    coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    sensor = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    map_api = (COMPONENT / "map_api.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")

    assert 'snapshot["task_resume"] = task_resume_decision(' in coordinator
    assert "retained_session=(" in coordinator
    assert '"navimower.continue_task",' in coordinator
    assert '"resume_available"' in sensor
    assert '"resume_strategy"' in sensor
    assert '"resume_reason"' in sensor
    assert '"resume_evidence"' in sensor
    assert '"task_resume": dict((coordinator.data or {}).get("task_resume") or {})' in map_api
    assert '"task_progress": entity_id("sensor", "task_progress")' in map_api
    assert '"task_resume": deepcopy(data.get("task_resume") or {})' in diagnostics


def test_beta37_resume_decision_semantics() -> None:
    code = textwrap.dedent(
        r"""
        import importlib.util
        from pathlib import Path
        import sys
        import types

        root = Path.cwd()

        def module(name):
            value = types.ModuleType(name)
            sys.modules[name] = value
            return value

        module("custom_components")
        navimower = module("custom_components.navimower")
        navimower.__path__ = [str(root / "custom_components" / "navimower")]

        const = module("custom_components.navimower.const")
        const.ACTIVITY_DOCKED = "docked"
        const.ACTIVITY_ERROR = "error"
        const.ACTIVITY_MOWING = "mowing"
        const.ACTIVITY_PAUSED = "paused"
        const.ACTIVITY_RETURNING = "returning"
        const.MAP_EDIT_STATES = {"0202", "0258"}
        const.STATE_PAUSED = "0211"

        spec = importlib.util.spec_from_file_location(
            "custom_components.navimower.task_resume",
            root / "custom_components" / "navimower" / "task_resume.py",
        )
        resume = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = resume
        spec.loader.exec_module(resume)

        incomplete = {
            "zone_ids": [1, 2],
            "task_zone_completion_confirmed": [1],
            "completed": None,
        }
        completed = {
            "zone_ids": [1, 2],
            "task_zone_completion_confirmed": [1, 2],
            "completed": True,
        }
        ordered = {
            "zone_ids": [1, 2],
            "remaining_zone_ids": [2],
            "completed_zone_ids": [1],
            "resumable": True,
            "complete": False,
            "superseded_at": None,
        }

        result = resume.task_resume_decision(
            {
                "activity": "returning",
                "mowing_progress": 45,
                "totals": {"task_zone_ids": [1, 2]},
                "last_ordered_run": ordered,
            },
            active_session=incomplete,
        )
        assert result["available"] is True
        assert result["strategy"] == "ordered_run"

        result = resume.task_resume_decision(
            {"activity": "returning", "mowing_progress": 45},
            active_session=incomplete,
        )
        assert result["available"] is True
        assert result["strategy"] == "vendor"

        result = resume.task_resume_decision(
            {"activity": "error", "mowing_progress": 45},
            active_session=incomplete,
        )
        assert result["available"] is True
        assert result["strategy"] == "vendor"

        result = resume.task_resume_decision(
            {"activity": "docked", "docked": True, "mowing_progress": 45},
            retained_session=incomplete,
        )
        assert result["available"] is True
        assert result["strategy"] == "vendor"

        result = resume.task_resume_decision(
            {
                "activity": "docked",
                "docked": True,
                "mowing_progress": 100,
                "last_ordered_run": ordered,
            },
            retained_session=completed,
        )
        assert result["available"] is False
        assert result["reason"] == "task_complete"

        result = resume.task_resume_decision(
            {"state_code": "0202", "activity": "paused", "mowing_progress": 20},
            active_session=incomplete,
        )
        assert result["available"] is False
        assert result["reason"] == "map_edit"

        result = resume.task_resume_decision(
            {"activity": "mowing", "mowing_progress": 20},
            active_session=incomplete,
        )
        assert result["available"] is False
        assert result["reason"] == "already_mowing"

        result = resume.task_resume_decision(
            {
                "activity": "returning",
                "mowing_progress": 20,
                "last_ordered_run": ordered,
            },
            active_session={
                "zone_ids": [9],
                "task_zone_completion_confirmed": [],
                "completed": None,
            },
        )
        assert result["available"] is True
        assert result["strategy"] == "vendor"
        assert "ordered_run_unconfirmed_for_current_task" in result["evidence"]

        result = resume.task_resume_decision(
            {"activity": "paused", "state_code": "0211"},
        )
        assert result["available"] is True
        assert result["strategy"] == "vendor"

        result = resume.task_resume_decision(
            {"activity": "docked", "docked": True},
        )
        assert result["available"] is False
        assert result["reason"] == "no_resumable_task_evidence"
        """
    )
    completed_process = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed_process.returncode == 0, (
        completed_process.stdout + completed_process.stderr
    )
