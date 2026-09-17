"""Current-cycle source arbitration must preserve zone and segment boundaries."""
from test_vendor_trail_store import render


def test_vendor_geometry_replaces_mqtt_for_same_zone_only():
    source = {
        "points": [
            [1, 0, 0, 0, "mowing", 4, 5, 431],
            [2, 1, 0, 0, "mowing", 4, 5, 431],
            [3, 5, 0, 0, "mowing", 4, 5, 436],
            [4, 6, 0, 0, "mowing", 4, 5, 436],
            [5, 7, 0, 0, "mowing", 4, 5, 431],
            [6, 8, 0, 0, "mowing", 4, 5, 436],
            [7, 9, 0, 0, "mowing", 4, 5, 436],
        ],
        "segment_starts_ms": [1, 3],
        "zone_ids": [431, 436],
    }
    filtered = render.filter_current_cycle_source(source, {431}, [])
    assert [point[7] for point in filtered["points"]] == [436]*4
    assert filtered["segment_starts_ms"] == [3, 6]
    assert filtered["zone_ids"] == [436]
