"""Regression tests for Navimower 0.4.4-beta1 map georeference support."""
from __future__ import annotations

import json

from custom_components.navimower.georeference import (
    georeference_from_geometry,
    georeference_from_plain_map_detail,
    local_xy_to_wgs84,
    validate_georeference,
)


_MAP_GEOMETRY = {
    "map_circle_center": [-6.191056109109541, 9.324995591152359],
    "center_gps": [24.638561248779297, 58.384281158447266],
    "map_north_offset": 0.5977016091346741,
    "origin_gps": [24.638547897338867, 58.38420104980469],
    "ne_gps": [24.638938903808594, 58.384521484375],
    "sw_gps": [24.6379451751709, 58.3840446472168],
}

_LOCATION = {
    "posture_x": "0.184",
    "posture_y": "-2.768",
    "latitude": "58.3841591",
    "longitude": "24.6385326",
    "report_time": "1785252961",
}


def test_georeference_normalizes_vendor_map_fields() -> None:
    georeference = georeference_from_geometry(_MAP_GEOMETRY)

    assert georeference is not None
    assert georeference["schema_version"] == 1
    assert georeference["source"] == "vendor_map_detail"
    assert georeference["reference"] == {
        "local_x": -6.191056109109541,
        "local_y": 9.324995591152359,
        "latitude": 58.384281158447266,
        "longitude": 24.638561248779297,
    }
    assert georeference["rotation_rad"] == 0.5977016091346741
    assert georeference["origin"] == {
        "latitude": 58.38420104980469,
        "longitude": 24.638547897338867,
    }
    assert georeference["bounds"]["north_east"] == {
        "latitude": 58.384521484375,
        "longitude": 24.638938903808594,
    }
    assert georeference["bounds"]["south_west"] == {
        "latitude": 58.3840446472168,
        "longitude": 24.6379451751709,
    }


def test_plain_map_detail_extracts_same_georeference() -> None:
    payload = {"map_detail": json.dumps({**_MAP_GEOMETRY, "sub_maps": []})}
    assert georeference_from_plain_map_detail(payload) == georeference_from_geometry(
        _MAP_GEOMETRY
    )


def test_local_xy_transform_matches_vendor_location() -> None:
    georeference = georeference_from_geometry(_MAP_GEOMETRY)
    assert georeference is not None

    point = local_xy_to_wgs84(georeference, 0.184, -2.768)
    assert point is not None
    latitude, longitude = point
    assert abs(latitude - float(_LOCATION["latitude"])) < 0.000003
    assert abs(longitude - float(_LOCATION["longitude"])) < 0.000003

    validated = validate_georeference(georeference, _LOCATION)
    assert validated is not None
    assert validated["validation"]["status"] == "validated"
    assert validated["validation"]["valid"] is True
    assert validated["validation"]["error_m"] < 0.2
    assert validated["validation"]["report_time"] == "1785252961"


def test_validation_detects_large_mismatch() -> None:
    georeference = georeference_from_geometry(_MAP_GEOMETRY)
    assert georeference is not None
    location = {**_LOCATION, "latitude": str(float(_LOCATION["latitude"]) + 0.001)}

    validated = validate_georeference(georeference, location)
    assert validated is not None
    assert validated["validation"]["status"] == "mismatch"
    assert validated["validation"]["valid"] is False
    assert validated["validation"]["error_m"] > 2.0


def test_validation_is_unavailable_without_complete_location_pair() -> None:
    georeference = georeference_from_geometry(_MAP_GEOMETRY)
    assert georeference is not None

    validated = validate_georeference(georeference, {"posture_x": 1, "posture_y": 2})
    assert validated is not None
    assert validated["validation"] == {
        "status": "unavailable",
        "valid": None,
    }


def test_incomplete_map_metadata_does_not_invent_georeference() -> None:
    assert georeference_from_geometry({"map_north_offset": 0.5}) is None
