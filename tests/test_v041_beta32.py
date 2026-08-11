"""Regression contracts for Navimower 0.4.1-beta32."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta32_manifest_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta32"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta32.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta32")
    for phrase in (
        "Latest notification",
        "url",
        "Legacy Map Camera",
        "deprecated",
        "stable/latest",
    ):
        assert phrase in notes


def test_beta32_notification_url_is_not_normalized_or_exposed() -> None:
    source = (COMPONENT / "beta26_runtime.py").read_text()
    ast.parse(source)
    normalize = source[
        source.index("def _normalize_item"):
        source.index("def _normalize_response")
    ]
    decorate = source[
        source.index("def _decorate_snapshot"):
        source.index("def _refresh_notification_cache")
    ]
    sensor = source[
        source.index("def _install_notification_sensor"):
        source.index("def _mark_map_camera_legacy")
    ]
    assert '"url"' not in normalize
    assert "notification_url" not in decorate
    assert '"url"' not in sensor


def test_beta32_cache_keeps_only_normalized_notification_data() -> None:
    source = (COMPONENT / "beta26_runtime.py").read_text()
    refresh = source[
        source.index("def _refresh_notification_cache"):
        source.index("def _install_notification_sensor")
    ]
    assert "normalized = _normalize_response(response)" in refresh
    assert '"list": deepcopy(normalized["list"])' in refresh
    assert '"has_history_message": normalized.get("has_history_message")' in refresh
    assert "coordinator._beta26_notification_cache = response" not in refresh


def test_beta32_sensor_is_named_latest_notification_without_id_break() -> None:
    source = (COMPONENT / "beta26_runtime.py").read_text()
    sensor = source[source.index("def _install_notification_sensor"):]
    assert 'key="notification"' in sensor
    assert 'name="Latest notification"' in sensor
    assert "_NOTIFICATION_ATTR_HISTORY_LIMIT = 5" in source


def test_beta32_map_camera_is_marked_legacy_without_unique_id_change() -> None:
    source = (COMPONENT / "beta26_runtime.py").read_text()
    legacy = source[
        source.index("def _mark_map_camera_legacy"):
        source.index("def install_beta26_runtime")
    ]
    assert 'NavimowMapCamera._attr_name = "Legacy Map Camera"' in legacy
    assert "NavimowMapCamera._attr_translation_key = None" in legacy

    camera = (COMPONENT / "camera.py").read_text()
    assert 'NavimowEntity.__init__(self, coordinator, "map")' in camera
