"""Regression tests for per-type MQTT location source-time ordering."""
from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "navimower"
    / "location.py"
)
spec = importlib.util.spec_from_file_location("navimower_location_ordering", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

parse_location_payload = module.parse_location_payload


def _work_position(action: int, sub_action: int, mode: int, zone: int, progress: int) -> str:
    return "".join(
        int(value).to_bytes(4, "big", signed=True).hex()
        for value in (action, sub_action, mode, zone, progress)
    )


def test_type1_late_pose_is_rejected_without_rewinding_position() -> None:
    cache: dict[str, dict] = {}
    first = parse_location_payload(
        cache,
        "dev",
        [{"type": 1, "time": 200_000, "postureX": 20, "postureY": 30, "postureTheta": 1, "vehicleState": 4}],
    )
    assert first is not None
    assert first["x"] == 20
    assert first["_pose_updated"] is True
    assert first["_state_updated"] is True

    late = parse_location_payload(
        cache,
        "dev",
        [{"type": 1, "time": 100_000, "postureX": 10, "postureY": 15, "postureTheta": 2, "vehicleState": 3}],
    )
    assert late is None
    row = cache["dev"]
    assert row["x"] == 20
    assert row["vehicle_state"] == 4
    assert row["_late_rejected_by_type"]["1"] == 1
    assert row["_late_rejected_message_types"] == [1]


def test_type2_late_work_packet_cannot_restore_old_zone() -> None:
    cache: dict[str, dict] = {}
    current = parse_location_payload(
        cache,
        "dev",
        [{
            "type": 2,
            "time": 200_000,
            "currentMowBoundary": 37,
            "currentMowProgress": 5000,
            "mapWorkPosition": _work_position(5, 0, 0, 37, 50),
        }],
    )
    assert current is not None
    assert current["mow_boundary"] == 37
    assert current["work_target_zone"] == 37
    assert current["_work_target_updated"] is True
    assert current["_action_updated"] is True

    late = parse_location_payload(
        cache,
        "dev",
        [{
            "type": 2,
            "time": 150_000,
            "currentMowBoundary": 36,
            "currentMowProgress": 9000,
            "mapWorkPosition": _work_position(5, 0, 0, 36, 90),
        }],
    )
    assert late is None
    row = cache["dev"]
    assert row["mow_boundary"] == 37
    assert row["work_target_zone"] == 37
    assert row["work_progress"] == 50
    assert row["_late_rejected_by_type"]["2"] == 1


def test_type3_late_partition_selection_is_rejected() -> None:
    cache: dict[str, dict] = {}
    fresh = parse_location_payload(
        cache,
        "dev",
        [{"type": 3, "time": 300_000, "partitionIds": [37]}],
    )
    assert fresh is not None
    assert fresh["partition_ids"] == [37]
    assert fresh["_partition_ids_updated"] is True

    late = parse_location_payload(
        cache,
        "dev",
        [{"type": 3, "time": 250_000, "partitionIds": [36]}],
    )
    assert late is None
    assert cache["dev"]["partition_ids"] == [37]
    assert cache["dev"]["_late_rejected_by_type"]["3"] == 1


def test_type4_late_delay_state_is_rejected() -> None:
    cache: dict[str, dict] = {}
    fresh = parse_location_payload(
        cache,
        "dev",
        [{"type": 4, "time": 400_000, "taskDelay": False, "vehicleState": 4}],
    )
    assert fresh is not None
    assert fresh["task_delay"] is False
    assert fresh["vehicle_state"] == 4
    assert fresh["_task_delay_updated"] is True
    assert fresh["_state_updated"] is True

    late = parse_location_payload(
        cache,
        "dev",
        [{"type": 4, "time": 350_000, "taskDelay": True, "vehicleState": 3}],
    )
    assert late is None
    assert cache["dev"]["task_delay"] is False
    assert cache["dev"]["vehicle_state"] == 4
    assert cache["dev"]["_late_rejected_by_type"]["4"] == 1


def test_equal_timestamp_is_accepted_for_partial_vendor_updates() -> None:
    cache: dict[str, dict] = {}
    first = parse_location_payload(
        cache,
        "dev",
        [{"type": 3, "time": 500_000, "partitionIds": [36]}],
    )
    assert first is not None
    second = parse_location_payload(
        cache,
        "dev",
        [{"type": 3, "time": 500_000, "partitionIds": [37]}],
    )
    assert second is not None
    assert second["partition_ids"] == [37]
    assert second["_late_rejected_by_type"].get("3", 0) == 0


def test_missing_source_time_remains_backward_compatible() -> None:
    cache: dict[str, dict] = {}
    row = parse_location_payload(
        cache,
        "dev",
        [{"type": 3, "partitionIds": [37]}],
    )
    assert row is not None
    assert row["partition_ids"] == [37]
    assert row["_partition_ids_updated"] is True
    assert "3" not in row["_source_time_by_type"]


def test_batch_final_state_is_newest_even_when_newer_arrives_first() -> None:
    cache: dict[str, dict] = {}
    row = parse_location_payload(
        cache,
        "dev",
        [
            {"type": 3, "time": 700_000, "partitionIds": [37]},
            {"type": 3, "time": 600_000, "partitionIds": [36]},
        ],
    )
    assert row is not None
    assert row["partition_ids"] == [37]
    assert row["_late_rejected_by_type"]["3"] == 1
