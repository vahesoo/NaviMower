"""Regression tests for Navimower 0.4.4-beta4 multi-mower frontend metadata."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from custom_components.navimower.georeference import offset_wgs84
from custom_components.navimower.multi_mower import build_site_payload

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _georeference(latitude: float, longitude: float) -> dict:
    return {
        "schema_version": 2,
        "status": "validated",
        "source": "synthetic",
        "reference": {
            "local_x": 0.0,
            "local_y": 0.0,
            "latitude": latitude,
            "longitude": longitude,
        },
        "rotation_rad": 0.0,
    }


def _coordinator(entry_id: str, georeference: dict):
    return SimpleNamespace(
        data={
            "name": entry_id,
            "model": "test",
            "vehicle_type": 1,
            "georeference": georeference,
            "map": {
                "revision": f"map-{entry_id}",
                "zones": [
                    {
                        "id": 1,
                        "polygon": [
                            [0.0, 0.0],
                            [10.0, 0.0],
                            [10.0, 10.0],
                            [0.0, 10.0],
                        ],
                    }
                ],
            },
        },
        entry=SimpleNamespace(title=entry_id, data={"model": "test"}),
    )


def test_beta4_release_notes_remain_available() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.4-beta4.md").read_text(
        encoding="utf-8"
    )
    assert notes.startswith("title: Navimower 0.4.4-beta4")
    assert "west-to-east" in notes
    assert "notification" in notes.lower()
    assert "sessions_api_path" in notes


def test_site_members_are_preordered_west_to_east() -> None:
    root = (58.0, 24.0)
    west = offset_wgs84(root[0], root[1], -80.0, 0.0)
    east = offset_wgs84(root[0], root[1], 100.0, 0.0)
    assert west is not None and east is not None

    payload = build_site_payload(
        "root",
        {
            "east": _coordinator("east", _georeference(east[0], east[1])),
            "root": _coordinator("root", _georeference(root[0], root[1])),
            "west": _coordinator("west", _georeference(west[0], west[1])),
        },
    )

    assert payload["multi_mower"] is True
    assert payload["member_order"] == "west_to_east"
    assert [member["entry_id"] for member in payload["members"]] == [
        "west",
        "root",
        "east",
    ]
    assert [member["display_order"] for member in payload["members"]] == [0, 1, 2]
    centers = [member["site_center"]["east"] for member in payload["members"]]
    assert centers == sorted(centers)


def test_frontend_metadata_exposes_multi_mower_fast_paths() -> None:
    source = (COMPONENT / "map_api.py").read_text(encoding="utf-8")

    assert '"map_api_path": f"/api/navimower/map/{entry_id}"' in source
    assert '"sessions_api_path": f"/api/navimower/sessions/{entry_id}"' in source
    assert '"session_render_api_path_template": (' in source
    assert '"notification": entity_id("sensor", "notification")' in source
    assert '"site_api_path": f"/api/navimower/site/{entry_id}"' in source
