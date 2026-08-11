"""Regression contracts for Navimower 0.4.1-beta28."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _beta_number() -> int:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    return int(manifest["version"].rsplit("beta", 1)[1])


def test_beta28_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    version = manifest["version"]
    assert version.startswith("0.4.1-beta")
    assert int(version.rsplit("beta", 1)[1]) >= 28
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta28.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta28")
    for phrase in (
        "translation keys",
        "Work status",
        "No more messages",
        "Failed to load new messages",
        "skipEncryption",
        "/mowerbot/",
        "Download diagnostics",
    ):
        assert phrase in notes


def test_beta28_translation_key_ranking_contract() -> None:
    source = (COMPONENT / "notification_feed_discovery.py").read_text()
    ast.parse(source)
    for phrase in (
        "_STRONG_ANCHORS",
        "_WEAK_ANCHORS",
        "_GENERIC_ANCHORS",
        "_translation_rows",
        "_ranking_keys",
        'if row.get("anchor") not in _STRONG_ANCHORS:',
        "_MAX_CONTEXTS_PER_TERM = 4",
        "_MAX_DYNAMIC_ASSETS = 6",
        '"generic_zero_score_anchors"',
        '"translation_key_map"',
        '"ranking_translation_keys"',
    ):
        assert phrase in source
    assert "All is deliberately not a request-context term" in source
    assert "All has no score and cannot select a chunk" in source


def test_beta28_extracts_request_structure_from_target_chunks() -> None:
    source = (COMPONENT / "notification_feed_discovery.py").read_text()
    for phrase in (
        'r"[\\\"\'](/mowerbot/',
        "skipEncryption",
        "sendEncryptionData",
        "callNative",
        "sendMessageToNative",
        '"mowerbot_requests"',
        '"http_methods"',
        '"skip_encryption"',
        '"object_keys"',
        '"target_score"',
    ):
        assert phrase in source
    assert "access_token" not in source
    assert "vehicle_sn" not in source
    assert "device_id" not in source


def test_beta28_download_discovery_retires_after_exact_feed_recovery() -> None:
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    action = (COMPONENT / "action_diagnostics.py").read_text()
    if _beta_number() >= 30:
        assert "notification_feed_probe" not in diagnostics
        assert "notification_history_probe" not in diagnostics
        assert "notification_feed_discovery" not in diagnostics
        assert "probe_main_notification_feed" not in diagnostics
        assert "coordinator.client.notification_feed" not in diagnostics
    elif _beta_number() >= 29:
        assert "notification_feed_probe" in diagnostics
        assert "notification_history_probe" not in diagnostics
        assert "notification_feed_discovery" not in diagnostics
        assert "probe_main_notification_feed" not in diagnostics
        assert "coordinator.client.notification_feed" in diagnostics
    else:
        assert "notification_history_probe" in diagnostics
        assert "notification_feed_discovery" in diagnostics
        assert "probe_main_notification_feed" in diagnostics
        assert "coordinator.client.notification_history" in diagnostics
    assert "probe_main_notification_feed" not in action
    assert "notification_feed_discovery" not in action
