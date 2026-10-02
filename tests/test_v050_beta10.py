"""Release regressions for 0.5.0-beta10 zone-constrained vendor trails."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
import types

from test_vendor_trail_store import (
    DiskStorage,
    Hass,
    History,
    NOW,
    ZONES,
    geometry,
    observe,
    session,
)

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"
MANIFEST = COMPONENT / "manifest.json"

PACKAGE = "beta10_zone_guard"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package

guard = importlib.import_module(f"{PACKAGE}.zone_segment_guard")
store_module = importlib.import_module(f"{PACKAGE}.vendor_trail_store")
vendor = importlib.import_module(f"{PACKAGE}.vendor_trail")


def test_beta10_version_and_notes() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta10"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta10.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "zone polygon",
        "1 m",
        "raw vendor points",
        "5 m",
        "Dock",
    ):
        assert marker in notes


def test_long_sparse_edge_inside_zone_is_kept_regardless_of_distance() -> None:
    polygon = [[0, 0], [30, 0], [30, 10], [0, 10]]
    assert guard.segment_within_zone_tolerance([1, 5], [29, 5], polygon)
    assert guard.zone_guard_break_indices([[1, 5], [29, 5]], polygon) == set()


def test_concave_zone_rejects_shortcut_through_unmapped_notch() -> None:
    # Both endpoints are inside the U-shaped zone, but the straight line crosses
    # a 2 m deep outside notch. Endpoint-only checks would get this wrong.
    polygon = [
        [0, 0], [10, 0], [10, 10], [7, 10],
        [7, 4], [3, 4], [3, 10], [0, 10],
    ]
    assert guard.point_in_polygon(2, 8, polygon)
    assert guard.point_in_polygon(8, 8, polygon)
    assert not guard.segment_within_zone_tolerance([2, 8], [8, 8], polygon)
    assert guard.zone_guard_break_indices([[2, 8], [8, 8]], polygon) == {1}


def test_one_metre_boundary_tolerance_is_applied_to_whole_edge() -> None:
    polygon = [[0, 0], [10, 0], [10, 10], [0, 10]]
    assert guard.segment_within_zone_tolerance([-0.8, 5], [10.8, 5], polygon)
    assert not guard.segment_within_zone_tolerance([-1.2, 5], [10.8, 5], polygon)


def _store(tmp_path):
    return store_module.VendorTrailStore(
        Hass(),
        "mower",
        storage=DiskStorage(tmp_path / "store.json"),
    )


def test_checkpoint_uses_polygon_guard_without_dropping_raw_points(tmp_path) -> None:
    store = _store(tmp_path)
    observe(store)
    row = geometry(end=1)
    row["points"] = [[1.0, 5.0], [29.0, 5.0]]
    assert store.accept(row)
    original = deepcopy(store.records[92]["points"])

    asyncio.run(
        store.async_artifacts(
            0.25,
            build=True,
            zone_ids={92},
            map_zones=ZONES,
        )
    )
    record = store.records[92]
    assert record["points"] == original
    assert record["future_gap_break_indices"] == []
    assert record["gap_guard_mode"] == "zone_polygon"
    assert record["gap_guard_tolerance_m"] == 1.0
    assert record["gap_guard_threshold_m"] is None
    assert record["gap_guard_version"] == 3
    assert len(store.hass.builds[-1]["segment_starts_ms"]) == 1


def test_polygon_invalid_edge_is_split_but_points_survive_restart(tmp_path) -> None:
    store = _store(tmp_path)
    concave = [
        {
            "id": 92,
            "area": 80,
            "polygon": [
                [0, 0], [10, 0], [10, 10], [7, 10],
                [7, 4], [3, 4], [3, 10], [0, 10],
            ],
        }
    ]
    observe(store, zones=concave)
    row = geometry(end=1)
    row["points"] = [[2.0, 8.0], [8.0, 8.0]]
    assert store.accept(row)

    asyncio.run(
        store.async_artifacts(
            0.25,
            build=True,
            zone_ids={92},
            map_zones=concave,
        )
    )
    assert store.records[92]["future_gap_break_indices"] == [1]
    assert store.records[92]["points"] == [[2.0, 8.0], [8.0, 8.0]]

    asyncio.run(store.async_flush())
    restored = store_module.VendorTrailStore(
        Hass(),
        "mower",
        storage=store.storage,
    )
    asyncio.run(restored.async_load())
    assert restored.records[92]["points"] == [[2.0, 8.0], [8.0, 8.0]]


def test_live_tail_no_longer_splits_merely_because_points_are_over_5m_apart(tmp_path) -> None:
    store = _store(tmp_path)
    observe(store)
    row = geometry(end=1)
    row["points"] = [[1.0, 5.0], [2.0, 5.0]]
    assert store.accept(row)
    asyncio.run(
        store.async_artifacts(
            0.25,
            build=True,
            zone_ids={92},
            map_zones=ZONES,
        )
    )

    live = {
        "id": "live",
        "points": [
            [NOW + 1_000, 2.0, 5.0, 0, "mowing", 4, 5, 92],
            [NOW + 2_000, 10.0, 5.0, 0, "mowing", 4, 5, 92],
            [NOW + 3_000, 20.0, 5.0, 0, "mowing", 4, 5, 92],
            [NOW + 4_000, 29.0, 5.0, 0, "mowing", 4, 5, 92],
        ],
        "segment_starts_ms": [NOW + 1_000],
    }
    snapshot = {"map": {"zones": ZONES}}
    store.update_live_tail(snapshot, live)
    assert store.live_tail(92) == [[
        [2.0, 5.0], [10.0, 5.0], [20.0, 5.0], [29.0, 5.0],
    ]]


def test_runtime_checkpoint_passes_map_zones_to_store() -> None:
    source = (COMPONENT / "map_artifacts.py").read_text(encoding="utf-8")
    assert 'map_zones = ((data.get("map") or {}).get("zones") or [])' in source
    assert "map_zones=map_zones" in source


def test_store_no_longer_uses_five_metre_live_tail_split() -> None:
    source = (COMPONENT / "vendor_trail_store.py").read_text(encoding="utf-8")
    live = source[source.index("def update_live_tail"):]
    assert "math.hypot(point[1]-previous[1], point[2]-previous[2]) > 5" not in live
    assert "segment_within_zone_tolerance(" in live
