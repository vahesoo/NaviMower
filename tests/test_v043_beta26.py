import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta26_identity_and_release_notes():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3-beta26"
    notes = (ROOT / ".github" / "release-notes" / "0.4.3-beta26.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.4.3-beta26")


def test_native_device_tracker_platform_is_loaded():
    init_source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    tracker_source = (COMPONENT / "device_tracker.py").read_text(encoding="utf-8")
    ast.parse(init_source)
    ast.parse(tracker_source)
    assert "Platform.DEVICE_TRACKER" in init_source
    assert "class NavimowerDeviceTracker(NavimowEntity, TrackerEntity):" in tracker_source
    assert '_attr_source_type = SourceType.GPS' in tracker_source
    assert 'NavimowEntity.__init__(self, coordinator, "location")' in tracker_source


def test_device_tracker_uses_existing_private_cloud_geographic_fields_only():
    tracker_source = (COMPONENT / "device_tracker.py").read_text(encoding="utf-8")
    coordinator_source = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    assert 'data.get("latitude")' in tracker_source
    assert 'data.get("longitude")' in tracker_source
    assert '"latitude": _as_float(_find(location, "latitude", "lat"))' in coordinator_source
    assert '"longitude": _as_float(_find(location, "longitude", "lng", "lon"))' in coordinator_source
    assert "mqtt" not in tracker_source.lower()
    assert "posture" not in tracker_source.lower()


def test_device_tracker_rejects_missing_or_invalid_coordinates():
    tracker_source = (COMPONENT / "device_tracker.py").read_text(encoding="utf-8")
    assert "minimum=-90.0, maximum=90.0" in tracker_source
    assert "minimum=-180.0, maximum=180.0" in tracker_source
    assert "self.latitude is not None" in tracker_source
    assert "self.longitude is not None" in tracker_source


def test_download_diagnostics_still_redacts_geographic_location():
    sanitize_source = (COMPONENT / "diagnostics_sanitize.py").read_text(encoding="utf-8")
    diagnostics_source = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    ast.parse(sanitize_source)
    ast.parse(diagnostics_source)
    for key in (
        '"latitude"',
        '"longitude"',
        '"last_latitude"',
        '"last_longitude"',
        '"origin_gps"',
        '"center_gps"',
        '"ne_gps"',
        '"sw_gps"',
    ):
        assert key in sanitize_source
    assert 'if "latitude" in normalized or "longitude" in normalized:' in sanitize_source
    assert 'if normalized.endswith("_gps") or normalized.startswith("gps_"):' in sanitize_source
    # Diagnostics may pass coordinator data through sanitize(), but must not
    # publish a dedicated unsanitized tracker-coordinate section.
    assert '"device_tracker_location"' not in diagnostics_source
