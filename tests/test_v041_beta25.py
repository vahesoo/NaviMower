"""Regression contracts for Navimower 0.4.1-beta25."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta25_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta25"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta25.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta25")
    for phrase in (
        "callNative",
        "sendEncryptionData",
        "lazy chunks",
        "Download diagnostics",
        "no credentials",
    ):
        assert phrase in notes


def test_beta25_generic_bridge_alias_extractor() -> None:
    source = (COMPONENT / "h5_discovery.py").read_text()
    ast.parse(source)
    for phrase in (
        "_GENERIC_BRIDGE_CALL_RE",
        "callNative",
        "sendEncryptionData",
        '"native_bridge_calls"',
        '"callee"',
        '"method"',
    ):
        assert phrase in source


def test_beta25_dynamic_chunk_discovery_is_thematic_and_bounded() -> None:
    source = (COMPONENT / "h5_discovery.py").read_text()
    ast.parse(source)
    for phrase in (
        "_JS_LITERAL_RE",
        "_dynamic_chunk_candidates",
        "_MAX_DYNAMIC_CHUNKS = 4",
        "_CHUNK_THEME_TERMS",
        "message",
        "notification",
        "history",
        '"dynamic_assets"',
        '"dynamic_chunk_candidates"',
        '"max_dynamic_chunks": _MAX_DYNAMIC_CHUNKS',
    ):
        assert phrase in source


def test_beta25_stays_download_only_public_and_read_only() -> None:
    h5 = (COMPONENT / "h5_discovery.py").read_text()
    diagnostics = (COMPONENT / "diagnostics.py").read_text()
    action = (COMPONENT / "action_diagnostics.py").read_text()
    coordinator = (COMPONENT / "coordinator.py").read_text()
    assert '"source": "home_assistant_download"' in h5
    assert '"public_unauthenticated_only": True' in h5
    assert 'method="GET"' in h5
    assert "probe_h5_frontend" in diagnostics
    assert "probe_h5_frontend" not in action
    assert "probe_h5_frontend" not in coordinator
    fetch_fn = h5[h5.index("def _fetch"):h5.index("def _extract_script_urls")]
    for forbidden in (
        "access_token",
        "refresh_token",
        "vehicle_sn",
        "device_id",
        "crypto.pack",
        "_auth_body",
    ):
        assert forbidden not in fetch_fn
