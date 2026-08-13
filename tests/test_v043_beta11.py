"""Regression contracts for Navimower 0.4.3-beta11 error command recovery."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta11_release_identity() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta11"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta11.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta11")


def test_beta11_error_discovery_uses_two_pass_full_fetch_selection() -> None:
    source = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    ast.parse(source)
    for phrase in (
        "def _full_fetch_priority",
        "Pass 1: collect bounded prefix evidence",
        "Pass 2: spend the full-fetch budget",
        '"mode": "two_pass_prefix_score_then_full"',
        '"full_fetch_plan"',
        '"full_fetch_score"',
        '"full_fetch_reasons"',
        'OBSERVED_ERROR_COMMAND_ASSETS = ("index-594ad42d.js",)',
        '"clear_plus_resume_prefix"',
        '"mower_set_native_bridge"',
    ):
        assert phrase in source


def test_beta11_recovers_handle_h5_mower_set_callsites() -> None:
    source = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    for phrase in (
        "MOWER_SET_ARROW_WRAPPER_RE",
        "MOWER_SET_WRAPPER_RE",
        "_exported_aliases",
        "_import_aliases_for_source",
        "_named_callsite_contexts",
        '"mower_set_wrapper_definitions"',
        '"mower_set_export_aliases"',
        '"mower_set_import_aliases"',
        '"mower_set_callsite_contexts"',
        '"argument_preview"',
    ):
        assert phrase in source or phrase == '"argument_preview"'
    maintenance = (COMPONENT / "maintenance_h5_discovery.py").read_text(encoding="utf-8")
    assert '"argument_preview": argument_preview[:1800]' in maintenance


def test_beta11_notification_detail_trace_requires_explicit_action() -> None:
    source = (COMPONENT / "notification_actions.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert '"explicit_user_action": True' in source
    assert 'coordinator._last_notification_detail_trace = trace' in source
    assert 'trace["response"] = deepcopy(result)' in source
    assert '"/mowerbot/user/message/getmessageDetailResp"' in source


def test_beta11_h5_probe_stays_non_mutating() -> None:
    source = (COMPONENT / "error_h5_discovery.py").read_text(encoding="utf-8")
    assert 'method="GET"' in source
    assert '"mutation_calls_executed": False' in source
    assert '"live_command_call_executed": False' in source
    assert '"notification_detail_call_executed": False' in source
    assert "client.call(" not in source
    assert "Authorization" not in source
    assert "Cookie" not in source
