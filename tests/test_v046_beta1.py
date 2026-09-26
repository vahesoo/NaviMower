"""Regression contract for Navimower 0.4.6-beta1."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
MANIFEST = COMPONENT / "manifest.json"
SCHEDULE = COMPONENT / "navimower_schedule.py"
LOGIC = COMPONENT / "schedule_logic.py"
DIAGNOSTICS = COMPONENT / "diagnostics.py"
SANITIZER = COMPONENT / "diagnostics_sanitize.py"


def test_beta1_version() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.6-beta1"


def test_beta1_wrong_zone_start_is_fail_closed_before_ownership() -> None:
    source = SCHEDULE.read_text(encoding="utf-8")
    confirm = source[
        source.index("    async def _confirm_pending"):
        source.index("    async def _enforce_closed_window")
    ]
    mismatch = confirm.index('evidence.get("state") == "zone_mismatch"')
    activate = confirm.index('self._runtime["active_zone_id"] = zone_id')
    assert mismatch < activate
    assert '"mow_start_zone_mismatch"' in confirm
    assert '"pending_command"] = None' in confirm
    assert "the requested queue slot was not started" in confirm


def test_beta1_start_classifier_uses_post_command_mqtt_zone_evidence() -> None:
    source = LOGIC.read_text(encoding="utf-8")
    block = source[
        source.index("def classify_schedule_mow_start"):
        source.index("def later_iso")
    ]
    assert '"work_target_zone"' in block
    assert '"mow_boundary"' in block
    assert "report >= sent.timestamp() - 1.0" in block
    assert '"state": "zone_mismatch"' in block


def test_beta1_download_diagnostics_omit_user_local_area_xy() -> None:
    source = DIAGNOSTICS.read_text(encoding="utf-8")
    assert 'options.pop("channels", None)' in source
    assert 'options.pop("custom_areas", None)' in source
    assert '"coordinates_included": False' in source
    sanitizer = SANITIZER.read_text(encoding="utf-8")
    assert "REDACTION_VERSION = 5" in sanitizer
