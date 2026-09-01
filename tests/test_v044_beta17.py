"""Regression tests for Navimower 0.4.4-beta17 geodesy reset."""
from __future__ import annotations

import json
import math
from pathlib import Path

from custom_components.navimower.georeference_geodesy_semantics import (
    offset_wgs84_ellipsoid,
    wgs84_offset_m_ellipsoid,
)

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _roundtrip(latitude: float, longitude: float, east: float, north: float) -> tuple[float, float]:
    target = offset_wgs84_ellipsoid(latitude, longitude, east, north)
    assert target is not None
    recovered = wgs84_offset_m_ellipsoid(latitude, longitude, target[0], target[1])
    assert recovered is not None
    return recovered


def test_beta17_version_release_notes_and_runtime_reset() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.4-beta17"

    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta17.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "WGS84 ellipsoid",
        "Zimbabwe",
        "Rio",
        "ETRS89",
        "raw",
        "X3",
    ):
        assert phrase in notes

    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    geodesy = runtime.index("install_georeference_geodesy_semantics()")
    georef = runtime.index("install_georeference_semantics()")
    static = runtime.index("install_georeference_static_anchor_semantics()")
    x3 = runtime.index("install_georeference_x3_bias_semantics()")
    diagnostics = runtime.index("install_georeference_diagnostics_semantics()")
    assert geodesy < georef < static < x3 < diagnostics
    assert "install_georeference_cartographic_semantics()" not in runtime


def test_wgs84_ellipsoid_roundtrip_estonia() -> None:
    east, north = _roundtrip(58.3842, 24.63855, 100.0, 100.0)
    assert math.isclose(east, 100.0, abs_tol=0.001)
    assert math.isclose(north, 100.0, abs_tol=0.001)


def test_wgs84_ellipsoid_roundtrip_rio() -> None:
    east, north = _roundtrip(-22.9068, -43.1729, 100.0, 100.0)
    assert math.isclose(east, 100.0, abs_tol=0.001)
    assert math.isclose(north, 100.0, abs_tol=0.001)


def test_wgs84_ellipsoid_roundtrip_zimbabwe() -> None:
    east, north = _roundtrip(-17.8252, 31.0335, 100.0, 100.0)
    assert math.isclose(east, 100.0, abs_tol=0.001)
    assert math.isclose(north, 100.0, abs_tol=0.001)


def test_low_latitude_northing_no_longer_uses_equatorial_sphere_radius() -> None:
    latitude = -17.8252
    longitude = 31.0335
    target = offset_wgs84_ellipsoid(latitude, longitude, 0.0, 100.0)
    assert target is not None

    # The retired spherical formula would interpret this latitude delta using
    # the equatorial radius. At Zimbabwe latitude that differs by roughly
    # 0.58 m over 100 m, which is too large for sub-metre map alignment.
    earth_radius = 6378137.0
    spherical_north = math.radians(target[0] - latitude) * earth_radius
    assert abs(spherical_north - 100.0) > 0.5


def test_dateline_uses_shortest_longitude_delta() -> None:
    offset = wgs84_offset_m_ellipsoid(0.0, 179.9999, 0.0, -179.9999)
    assert offset is not None
    assert 20.0 < offset[0] < 25.0
    assert abs(offset[1]) < 0.001
