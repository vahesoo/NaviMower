"""Historical regression tests for Navimower 0.4.4-beta2 diagnostics."""
from __future__ import annotations

from pathlib import Path

from custom_components.navimower.diagnostics_sanitize import sanitize

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta2_release_notes_remain_available() -> None:
    """Keep beta2 as historical coverage without pinning the current manifest."""
    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta2.md").read_text(
        encoding="utf-8"
    )
    assert notes.startswith("title: Navimower 0.4.4-beta2")
    assert "Georeference diagnostics" in notes
    assert "cached-only" in notes
    assert "redacted" in notes


def test_beta2_download_diagnostics_exports_runtime_georeference() -> None:
    source = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert '"georeference": sanitize(deepcopy(data.get("georeference")))' in source
    assert "physical GPS coordinates remain redacted" in source


def test_georeference_sanitizer_keeps_transform_and_validation_but_redacts_gps() -> None:
    georeference = {
        "schema_version": 1,
        "source": "vendor_map_detail",
        "reference": {
            "local_x": -6.191056109109541,
            "local_y": 9.324995591152359,
            "latitude": 58.384281158447266,
            "longitude": 24.638561248779297,
        },
        "rotation_rad": 0.5977016091346741,
        "origin": {
            "latitude": 58.38420104980469,
            "longitude": 24.638547897338867,
        },
        "bounds": {
            "north_east": {
                "latitude": 58.384521484375,
                "longitude": 24.638938903808594,
            },
            "south_west": {
                "latitude": 58.3840446472168,
                "longitude": 24.6379451751709,
            },
        },
        "validation": {
            "status": "validated",
            "valid": True,
            "error_m": 0.14,
            "limit_m": 2.0,
            "report_time": "1785252961",
        },
    }

    result = sanitize(georeference)

    assert result["schema_version"] == 1
    assert result["source"] == "vendor_map_detail"
    assert result["reference"]["local_x"] == georeference["reference"]["local_x"]
    assert result["reference"]["local_y"] == georeference["reference"]["local_y"]
    assert result["rotation_rad"] == georeference["rotation_rad"]
    assert result["validation"] == georeference["validation"]
    assert result["reference"]["latitude"] == "<redacted>"
    assert result["reference"]["longitude"] == "<redacted>"
    assert result["origin"]["latitude"] == "<redacted>"
    assert result["origin"]["longitude"] == "<redacted>"
    assert result["bounds"]["north_east"]["latitude"] == "<redacted>"
    assert result["bounds"]["north_east"]["longitude"] == "<redacted>"
    assert result["bounds"]["south_west"]["latitude"] == "<redacted>"
    assert result["bounds"]["south_west"]["longitude"] == "<redacted>"
