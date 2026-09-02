"""Release contract for Navimower 0.4.4-beta22 map-underlay backend."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from custom_components.navimower.diagnostics_sanitize import sanitize
from custom_components.navimower.map_underlay import (
    GoogleMapTilesManager,
    is_estonia_location,
    map_underlay_metadata,
    shared_google_maps_api_key,
)

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "navimower"


class _Entry:
    def __init__(self, entry_id: str, email: str, options: dict | None = None) -> None:
        self.entry_id = entry_id
        self.data = {"email": email}
        self.options = options or {}


class _ConfigEntries:
    def __init__(self, entries: list[_Entry]) -> None:
        self._entries = entries

    def async_entries(self, domain: str):  # noqa: ARG002
        return list(self._entries)


class _Hass:
    def __init__(self, entries: list[_Entry]) -> None:
        self.config_entries = _ConfigEntries(entries)
        self.data = {}


def test_manifest_and_release_notes() -> None:
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    assert manifest["version"] == "0.4.4-beta22"

    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta22.md").read_text()
    for token in (
        "Map underlay",
        "Google Map Tiles API key",
        "account-scoped",
        "estonia_hybrid",
        "api: configured",
        "never export",
    ):
        assert token.lower() in notes.lower()


def test_google_api_key_is_account_scoped() -> None:
    entries = [
        _Entry("a", "User@Example.com", {"google_maps_api_key": "shared-key"}),
        _Entry("b", "user@example.com"),
        _Entry("c", "other@example.com", {"google_maps_api_key": "other-key"}),
    ]
    assert shared_google_maps_api_key(entries, "USER@example.com") == "shared-key"
    assert shared_google_maps_api_key(entries, "other@example.com") == "other-key"
    assert shared_google_maps_api_key(entries, "missing@example.com") is None


def test_estonia_capabilities_use_georeference_without_exporting_coordinates() -> None:
    entry = _Entry("entry-ee", "user@example.com", {"google_maps_api_key": "secret-key"})
    hass = _Hass([entry])
    coordinator = SimpleNamespace(
        hass=hass,
        entry=entry,
        data={
            "georeference": {
                "status": "validated",
                "reference": {
                    "local_x": 0.0,
                    "local_y": 0.0,
                    "latitude": 59.0,
                    "longitude": 24.0,
                },
                "rotation_rad": 0.0,
            }
        },
    )

    metadata = map_underlay_metadata(coordinator)
    assert metadata["location"] == {"country_code": "EE"}
    assert metadata["map_underlays"]["estonia_orthophoto"]["available"] is True
    assert metadata["map_underlays"]["estonia_hybrid"]["available"] is True
    google = metadata["map_underlays"]["google_satellite"]
    assert google["configured"] is True
    assert google["available"] is True
    assert "{z}/{x}/{y}" in google["tile_api_path_template"]
    assert google["viewport_api_path"].endswith("/viewport")
    assert "secret-key" not in repr(metadata)
    assert "latitude" not in metadata["location"]
    assert "longitude" not in metadata["location"]


def test_estonia_bbox_and_google_diagnostics_are_privacy_safe() -> None:
    assert is_estonia_location(59.0, 24.0)
    assert not is_estonia_location(52.0, 13.0)

    manager = GoogleMapTilesManager()
    status = manager.diagnostics("account", configured=True)
    assert status["api"] == "configured"
    assert status["configured"] is True
    assert status["session_active"] is False
    assert "api_key" not in repr(status).lower()
    assert "session_token" not in repr(status).lower()

    sanitized = sanitize({"google_maps_api_key": "do-not-export"})
    assert sanitized["google_maps_api_key"] == "<redacted>"


def test_backend_source_contracts() -> None:
    config_flow = (INTEGRATION / "config_flow.py").read_text()
    map_api = (INTEGRATION / "map_api.py").read_text()
    diagnostics = (INTEGRATION / "diagnostics.py").read_text()
    underlay = (INTEGRATION / "map_underlay.py").read_text()

    for token in (
        '"map_underlay"',
        "OPT_GOOGLE_MAPS_API_KEY",
        "TextSelectorType.PASSWORD",
        "async_validate_key",
        "clear_google_maps_api_key",
        "private_account_entries",
    ):
        assert token in config_flow

    for token in (
        "/api/navimower/underlay/google/{entry_id}/{z}/{x}/{y}",
        "/api/navimower/underlay/google/{entry_id}/viewport",
        "NavimowerGoogleTileView",
        "NavimowerGoogleViewportView",
        "copyright",
        "maxZoomRects",
        "requires_auth = True",
    ):
        assert token in map_api

    assert "map_underlay_diagnostics" in diagnostics
    assert "options.pop(OPT_GOOGLE_MAPS_API_KEY, None)" in diagnostics
    assert "GOOGLE_CREATE_SESSION_URL" in underlay
    assert '"mapType": "satellite"' in underlay
    assert "Cache-Control" in underlay
    assert "session_token" not in map_api
