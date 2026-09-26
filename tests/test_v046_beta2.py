"""Regression contract for Navimower 0.4.6-beta2."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta2_version() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.6-beta2"


def test_beta2_release_scope() -> None:
    services = (COMPONENT / "services.py").read_text(encoding="utf-8")
    notification = (COMPONENT / "notification_center.py").read_text(encoding="utf-8")
    assert "guard_managed_schedule_resume" in services
    assert "managed_schedule_active" in (COMPONENT / "task_resume.py").read_text(encoding="utf-8")
    assert 'self._active_task["zone_ids"] = list(observed_ids)' in notification
