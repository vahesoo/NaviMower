"""Regression contract for Navimower 0.4.2-beta6 runtime cleanup."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta6_release_history_remains_present() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"].startswith("0.4.2")
    notes = (ROOT / ".github" / "release-notes" / "0.4.2-beta6.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.2-beta6")
    assert "semantic runtime" in notes.lower()
    assert "beta-numbered runtime" in notes.lower()


def test_beta6_semantic_runtime_modules_parse_and_old_layers_are_gone() -> None:
    for name in (
        "runtime.py",
        "state_semantics.py",
        "capability_extensions.py",
        "navigation_fallback.py",
        "notification_feed.py",
    ):
        ast.parse((COMPONENT / name).read_text(encoding="utf-8"))

    for old in ("beta16_runtime.py", "beta17_runtime.py", "beta18_runtime.py", "beta26_runtime.py"):
        assert not (COMPONENT / old).exists()


def test_beta6_preserves_proven_runtime_contracts_under_semantic_names() -> None:
    state = (COMPONENT / "state_semantics.py").read_text(encoding="utf-8")
    assert '_STATE_IDLE = "0103"' in state
    assert '_STATE_FAULT = "0301"' in state
    assert 'MQTT_DOCKED_STATES.discard(_MQTT_STOPPED)' in state
    assert 'request_fast_refresh("MQTT state changed to Error")' in state

    capabilities = (COMPONENT / "capability_extensions.py").read_text(encoding="utf-8")
    assert '"i208 AWD"' in capabilities
    assert "append_or_coalesce" in capabilities
    assert "compact_route_points" in capabilities

    navigation = (COMPONENT / "navigation_fallback.py").read_text(encoding="utf-8")
    assert "choose_position(" in navigation
    assert "outside_count >= 2" in navigation
    assert 'position_source == "private_cloud"' in navigation

    feed = (COMPONENT / "notification_feed.py").read_text(encoding="utf-8")
    assert "vehicleMessageListField" in feed
    assert "VENDOR_NOTIFICATION_LIMIT" in feed
    assert "merge_notification_lists" in feed
    assert "refresh_notification_snapshot" in feed


def test_beta6_notification_read_invalidation_uses_semantic_cache_state() -> None:
    actions = (COMPONENT / "notification_actions.py").read_text(encoding="utf-8")
    assert "_notification_last_attempt_mono = None" in actions
    assert "_beta26_notification" not in actions
