"""Regression contracts for Navimower 0.4.1-beta22."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta22_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta22"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta22.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta22")
    for phrase in (
        "Download diagnostics unchanged",
        "navimower.export_diagnostics",
        "H5/Willand",
        "POST vs GET",
        "credentials are never placed in plaintext",
    ):
        assert phrase in notes


def test_beta22_native_download_remains_free_of_notification_probe() -> None:
    source = (COMPONENT / "diagnostics.py").read_text()
    ast.parse(source)
    assert "probe_event" not in source
    assert '"notification_event_probe"' not in source
    assert '"home_assistant_download"' in source


def test_beta22_action_uses_transport_probe() -> None:
    source = (COMPONENT / "action_diagnostics.py").read_text()
    ast.parse(source)
    assert "event_transport_probe" in source
    assert "probe_event_transports" in source
    assert 'document["notification_event_probe"]' in source
    assert '"navimower.export_diagnostics"' in source


def test_beta22_transport_probe_covers_host_method_and_encoding() -> None:
    source = (COMPONENT / "event_transport_probe.py").read_text()
    ast.parse(source)
    for phrase in (
        "navimow-{region}.ninebot.com",
        "navimow-h5-{region}.willand.com",
        '"p101_json_text_html"',
        '"p101_json_application_json"',
        '"p101_form"',
        '"p101_query"',
        '"plain_json"',
        '"plain_query"',
    ):
        assert phrase in source
    assert '("GET", "p101_query", True)' in source
    assert '("POST", "p101_json_text_html", True)' in source
    assert source.count('"/message/') >= 3
    assert source.count('"/push/') >= 2


def test_beta22_plain_variants_do_not_include_credentials() -> None:
    source = (COMPONENT / "event_transport_probe.py").read_text()
    plain_fn = source[source.index("def _plain_params"):source.index("def _body_summary")]
    assert "language" in plain_fn
    assert "pageSize" in plain_fn
    for forbidden in ("access_token", "uid", "device_id", "vehicle_sn"):
        assert forbidden not in plain_fn


def test_beta22_probe_remains_read_only_and_action_only() -> None:
    source = (COMPONENT / "event_transport_probe.py").read_text()
    coordinator = (COMPONENT / "coordinator.py").read_text()
    for forbidden in (
        "/vehicle/set/send",
        "/vehicle/set/save-set-data",
        "/map/index/save",
        "save_setting(",
        "mow_zones(",
    ):
        assert forbidden not in source
    assert "event_transport_probe" not in coordinator
    assert '"not_found_count"' in source
    assert '"http_status_counts"' in source
    assert '"native_download_diagnostics_unchanged": True' in source
