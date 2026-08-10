"""Regression contracts introduced with Navimower 0.4.1-beta24."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta24_release_notes_remain_available() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta24.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta24")
    for phrase in (
        "native bridge",
        "sendMessageToNative",
        "Download diagnostics",
        "public H5",
        "no credentials",
    ):
        assert phrase in notes


def test_beta24_h5_scanner_extracts_bridge_and_literal_routes() -> None:
    source = (COMPONENT / "h5_discovery.py").read_text()
    ast.parse(source)
    for phrase in (
        "_BRIDGE_OBJECT_RE",
        "_DIRECT_BRIDGE_RE",
        "_CALL_LITERAL_RE",
        "_PATH_LITERAL_RE",
        "native_bridge_methods",
        "native_bridge_callbacks",
        "http_call_literals",
        "path_literals",
        "contexts",
        "sendMessageToNative",
        "messageHandlers",
        "AndroidAndJs",
        "newMessages",
    ):
        assert phrase in source


def test_beta24_context_capture_remains_bounded() -> None:
    source = (COMPONENT / "h5_discovery.py").read_text()
    assert "_MAX_CONTEXTS" in source
    assert "_CONTEXT_RADIUS" in source
    assert '"max_contexts": _MAX_CONTEXTS' in source
    assert '"context_radius_chars": _CONTEXT_RADIUS' in source


def test_beta24_remains_public_read_only_and_action_free() -> None:
    h5 = (COMPONENT / "h5_discovery.py").read_text()
    action = (COMPONENT / "action_diagnostics.py").read_text()
    coordinator = (COMPONENT / "coordinator.py").read_text()
    assert '"public_unauthenticated_only": True' in h5
    assert 'method="GET"' in h5
    for forbidden in ("access_token", "refresh_token", "crypto.pack", "_auth_body"):
        assert forbidden not in h5[h5.index("def _fetch"):h5.index("def _extract_script_urls")]
    assert "probe_h5_frontend" not in action
    assert "probe_h5_frontend" not in coordinator
