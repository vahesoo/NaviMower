"""Regression tests for Navimower 0.4.4-beta3 universal georeference/site support."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from custom_components.navimower.georeference import (
    georeference_from_geometry,
    local_xy_to_wgs84,
    offset_wgs84,
    update_georeference,
)
from custom_components.navimower.multi_mower import build_site_payload

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _truth_georeference(
    latitude: float = 58.0,
    longitude: float = 24.0,
    rotation_rad: float = 0.6,
) -> dict:
    return {
        "schema_version": 2,
        "status": "validated",
        "source": "synthetic",
        "reference": {
            "local_x": 10.0,
            "local_y": -5.0,
            "latitude": latitude,
            "longitude": longitude,
        },
        "rotation_rad": rotation_rad,
    }


def _location(truth: dict, x: float, y: float, report_time: int) -> dict:
    point = local_xy_to_wgs84(truth, x, y)
    assert point is not None
    return {
        "posture_x": x,
        "posture_y": y,
        "latitude": point[0],
        "longitude": point[1],
        "report_time": str(report_time),
    }


def _coordinator(entry_id: str, georeference: dict, polygon=None):
    polygon = polygon or [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]
    return SimpleNamespace(
        data={
            "name": entry_id,
            "model": "test",
            "vehicle_type": 1,
            "georeference": georeference,
            "map": {
                "revision": f"map-{entry_id}",
                "zones": [{"id": 1, "polygon": polygon}],
            },
        },
        entry=SimpleNamespace(title=entry_id, data={"model": "test"}),
    )


def test_beta3_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.4-beta3"

    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta3.md").read_text(
        encoding="utf-8"
    )
    assert notes.startswith("title: Navimower 0.4.4-beta3")
    assert "Universal map georeference" in notes
    assert "/api/navimower/site/{entry_id}" in notes
    assert "500 m" in notes


def test_cloud_location_fit_learns_one_stable_transform() -> None:
    truth = _truth_georeference()
    geometry = {"revision": "1|100|200|v:300"}
    result = None
    for index, (x, y) in enumerate(
        [(0, 0), (3, 2), (6, -1), (9, 4), (12, 1), (15, 5), (18, -2)]
    ):
        result = update_georeference(geometry, _location(truth, x, y, index + 1))

    assert result is not None
    assert result["schema_version"] == 2
    assert result["source"] == "cloud_location_fit"
    assert result["status"] == "validated"
    assert result["map_revision"] == geometry["revision"]
    assert result["calibration"]["sample_count"] >= 5
    assert result["calibration"]["baseline_m"] >= 10.0
    assert result["calibration"]["rms_error_m"] < 0.05
    assert abs(result["rotation_rad"] - truth["rotation_rad"]) < 0.001

    expected = local_xy_to_wgs84(truth, 21.0, 3.0)
    actual = local_xy_to_wgs84(result, 21.0, 3.0)
    assert expected is not None and actual is not None
    assert abs(expected[0] - actual[0]) < 0.000001
    assert abs(expected[1] - actual[1]) < 0.000001


def test_unversioned_last_location_pair_is_not_used_for_learning() -> None:
    truth = _truth_georeference()
    geometry = {"revision": "map-a"}
    current = _location(truth, 0.0, 0.0, 1)
    far = _location(truth, 30.0, 10.0, 999)

    result = None
    for report_time in range(1, 7):
        result = update_georeference(
            geometry,
            {
                **current,
                "report_time": str(report_time),
                "last_posture_x": far["posture_x"],
                "last_posture_y": far["posture_y"],
                "last_latitude": far["latitude"],
                "last_longitude": far["longitude"],
            },
        )

    assert result is not None
    assert result["status"] == "learning"
    assert result["calibration"]["sample_count"] == 1
    assert result["calibration"]["baseline_m"] == 0.0


def test_map_revision_change_resets_learned_calibration() -> None:
    truth = _truth_georeference()
    geometry = {"revision": "map-a"}
    result = None
    for index, x in enumerate((0, 3, 6, 9, 12, 15)):
        result = update_georeference(geometry, _location(truth, x, index % 2, index))
    assert result is not None and result["status"] == "validated"

    geometry["revision"] = "map-b"
    result = update_georeference(geometry, _location(truth, 1, 1, 100))
    assert result is not None
    assert result["status"] == "learning"
    assert result["map_revision"] == "map-b"
    assert result["calibration"]["sample_count"] == 1


def test_vendor_map_detail_is_only_a_bootstrap_until_universal_fit_is_ready() -> None:
    vendor_geometry = {
        "map_circle_center": [-6.191056109109541, 9.324995591152359],
        "center_gps": [24.638561248779297, 58.384281158447266],
        "map_north_offset": 0.5977016091346741,
    }
    vendor = georeference_from_geometry(vendor_geometry)
    assert vendor is not None
    geometry = {"revision": "h2-map", "_vendor_georeference": vendor}
    location = {
        "posture_x": "0.184",
        "posture_y": "-2.768",
        "latitude": "58.3841591",
        "longitude": "24.6385326",
        "report_time": "1785252961",
    }

    result = update_georeference(geometry, location)
    assert result is not None
    assert result["schema_version"] == 2
    assert result["source"] == "vendor_map_detail"
    assert result["status"] == "validated"
    assert result["calibration"]["sample_count"] == 1
    assert result["vendor_hint"]["validation"]["valid"] is True


def test_site_payload_groups_only_validated_mowers_within_500m() -> None:
    root_geo = _truth_georeference()
    near_point = offset_wgs84(58.0, 24.0, 100.0, 0.0)
    far_point = offset_wgs84(58.0, 24.0, 650.0, 0.0)
    assert near_point is not None and far_point is not None
    near_geo = _truth_georeference(near_point[0], near_point[1], -0.2)
    far_geo = _truth_georeference(far_point[0], far_point[1], 0.1)

    payload = build_site_payload(
        "root",
        {
            "root": _coordinator("root", root_geo),
            "near": _coordinator("near", near_geo),
            "far": _coordinator("far", far_geo),
        },
    )

    assert payload["multi_mower"] is True
    assert [item["entry_id"] for item in payload["members"]] == ["root", "near"]
    assert payload["excluded_valid_count"] == 1
    assert payload["unresolved_count"] == 0
    assert payload["combined_svg_bounds"] is not None
    for member in payload["members"]:
        assert member["local_to_site_en"] is not None
        assert len(member["svg_matrix"]) == 6
        assert member["svg_bounds"] is not None


def test_site_grouping_is_anchor_relative_not_transitive() -> None:
    root_geo = _truth_georeference()
    middle_point = offset_wgs84(58.0, 24.0, 450.0, 0.0)
    chained_point = offset_wgs84(58.0, 24.0, 850.0, 0.0)
    assert middle_point is not None and chained_point is not None

    payload = build_site_payload(
        "root",
        {
            "root": _coordinator("root", root_geo),
            "middle": _coordinator(
                "middle", _truth_georeference(middle_point[0], middle_point[1])
            ),
            "chained": _coordinator(
                "chained", _truth_georeference(chained_point[0], chained_point[1])
            ),
        },
    )

    assert [item["entry_id"] for item in payload["members"]] == ["root", "middle"]
    assert payload["excluded_valid_count"] == 1


def test_map_api_advertises_and_registers_site_endpoint() -> None:
    source = (COMPONENT / "map_api.py").read_text(encoding="utf-8")
    assert '"site_api_path": f"/api/navimower/site/{entry_id}"' in source
    assert 'url = "/api/navimower/site/{entry_id}"' in source
    assert "hass.http.register_view(NavimowerSiteView())" in source
