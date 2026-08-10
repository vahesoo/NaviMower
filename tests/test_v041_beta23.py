"""Regression contracts for Navimower 0.4.1-beta23."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta23_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta23"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta23.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta23")
    for phrase in (
        "Download diagnostics",
        "H5 frontend",
        "JavaScript bundle",
        "public GETs",
        "navimower.export_diagnostics",
    ):
        assert phrase in notes


def test_beta23_download_diagnostics_runs_h5_discovery() -> None:
    source = (COMPONENT / "diagnostics.py").read_text()
    ast.parse(source)
    assert "probe_h5_frontend" in source
    assert 'document["h5_frontend_discovery"]' in source
    assert "async_add_executor_job" in source
    assert '"home_assistant_download"' in source


def test_beta23_action_has_no_notification_or_h5_probe() -> None:
    source = (COMPONENT / "action_diagnostics.py").read_text()
    ast.parse(source)
    assert "probe_event" not in source
    assert "probe_h5" not in source
    assert '"notification_event_probe"' not in source
    assert '"h5_frontend_discovery"' not in source
    assert '"navimower_diagnostics_latest.json"' in source


def test_beta23_h5_discovery_is_bounded_public_and_body_free() -> None:
    source = (COMPONENT / "h5_discovery.py").read_text()
    ast.parse(source)
    for phrase in (
        "_MAX_HTML_BYTES",
        "_MAX_JS_BYTES",
        "_MAX_SCRIPT_ASSETS = 6",
        "_TIMEOUT_SECONDS = 5",
        '"public_unauthenticated_only": True',
        '"source": "home_assistant_download"',
        "body_sha256",
        "script_urls",
        "base_url_candidates",
        "api_like_strings",
    ):
        assert phrase in source
    assert 'method="GET"' in source
    assert '"_text"' in source
    assert 'key != "_text"' in source


def test_beta23_h5_requests_never_send_credentials_or_mower_identity() -> None:
    source = (COMPONENT / "h5_discovery.py").read_text()
    fetch_fn = source[source.index("def _fetch"):source.index("def _extract_script_urls")]
    for forbidden in (
        "access_token",
        "refresh_token",
        "vehicle_sn",
        "device_id",
        "crypto.pack",
        "_auth_body",
    ):
        assert forbidden not in fetch_fn


def test_beta23_h5_discovery_remains_out_of_polling() -> None:
    coordinator = (COMPONENT / "coordinator.py").read_text()
    assert "probe_h5_frontend" not in coordinator
    assert "h5_frontend_discovery" not in coordinator
