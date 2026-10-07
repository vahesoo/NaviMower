from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "custom_components" / "navimower" / "position_trust.py"

spec = importlib.util.spec_from_file_location("navimower_position_trust_test", MODULE_PATH)
assert spec and spec.loader
trust = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = trust
spec.loader.exec_module(trust)


class Owner:
    def __init__(self) -> None:
        self._map_geometry = {
            "station": {"x": 1.0, "y": 2.0},
        }
        self._mqtt_last_update = 100.0
        self._position_trust_state = None


def snapshot(*, docked: bool) -> dict:
    return {
        "docked": docked,
        "activity": "docked" if docked else "mowing",
        "map": {"station": {"x": 1.0, "y": 2.0}},
    }


def test_docked_stale_far_mqtt_is_hidden_until_trusted_pose() -> None:
    owner = Owner()
    result = trust.prepare_position_candidates(
        owner,
        snapshot(docked=True),
        mqtt_position={"x": 20.0, "y": 30.0, "heading": 1.0},
        cloud_position=None,
        mqtt_pose_time=1_700_000_000,
        cloud_report_time=None,
        now_epoch=1_800_000_000,
        now_mono=100.0,
    )
    assert result["mqtt_position"] is None
    assert result["cloud_position"] is None
    assert result["position_override"]["available"] is False
    assert result["position_override"]["source"] == "startup_waiting"
    diag = trust.position_trust_diagnostics(owner)
    assert diag is not None
    assert diag["phase"] == "startup_waiting"
    assert diag["last_reason"] == "startup_waiting_for_station_consistent_pose"
    assert diag["station_distance_m"] > 2.0
    assert diag["coordinates_included"] is False


def test_docked_station_consistent_mqtt_keeps_real_heading_even_with_old_time() -> None:
    owner = Owner()
    result = trust.prepare_position_candidates(
        owner,
        snapshot(docked=True),
        mqtt_position={"x": 1.2, "y": 2.1, "heading": 4.7},
        cloud_position=None,
        mqtt_pose_time=1_700_000_000,
        cloud_report_time=None,
        now_epoch=1_800_000_000,
        now_mono=100.0,
    )
    assert result["mqtt_position"] == {"x": 1.2, "y": 2.1, "heading": 4.7}
    assert result["position_override"] is None
    diag = trust.position_trust_diagnostics(owner)
    assert diag is not None
    assert diag["ready"] is True
    assert diag["first_trusted_source"] == "official_mqtt"
    assert diag["last_reason"] == "docked_station_consistent"


def test_docked_far_mqtt_can_fall_back_to_real_cloud_pose_near_station() -> None:
    owner = Owner()
    result = trust.prepare_position_candidates(
        owner,
        snapshot(docked=True),
        mqtt_position={"x": 20.0, "y": 30.0, "heading": 1.0},
        cloud_position={"x": 1.1, "y": 2.0, "heading": 3.2},
        mqtt_pose_time=1_700_000_000,
        cloud_report_time=1_700_000_000,
        now_epoch=1_800_000_000,
        now_mono=100.0,
    )
    assert result["mqtt_position"] is None
    assert result["cloud_position"] == {"x": 1.1, "y": 2.0, "heading": 3.2}
    assert trust.position_trust_diagnostics(owner)["first_trusted_source"] == "private_cloud"


def test_active_startup_accepts_recent_vendor_timestamp() -> None:
    owner = Owner()
    result = trust.prepare_position_candidates(
        owner,
        snapshot(docked=False),
        mqtt_position={"x": 10.0, "y": 11.0, "heading": 0.5},
        cloud_position=None,
        mqtt_pose_time=1_799_999_995,
        cloud_report_time=None,
        now_epoch=1_800_000_000,
        now_mono=100.0,
    )
    assert result["mqtt_position"] is not None
    assert trust.position_trust_diagnostics(owner)["last_reason"] == "fresh_vendor_timestamp"


def test_departure_holds_last_dock_pose_against_old_far_candidate() -> None:
    owner = Owner()

    accepted = trust.prepare_position_candidates(
        owner,
        snapshot(docked=True),
        mqtt_position={"x": 1.1, "y": 2.0, "heading": 4.7},
        cloud_position=None,
        mqtt_pose_time=1_700_000_000,
        cloud_report_time=None,
        now_epoch=1_800_000_000,
        now_mono=100.0,
    )
    canonical_state = {
        "navigation": {
            "position": {
                "value": accepted["mqtt_position"],
                "source": "official_mqtt",
            }
        }
    }
    trust.record_position_result(owner, snapshot(docked=True), canonical_state)

    departing = trust.prepare_position_candidates(
        owner,
        snapshot(docked=False),
        mqtt_position={"x": 20.0, "y": 30.0, "heading": 1.0},
        cloud_position=None,
        mqtt_pose_time=1_700_000_000,
        cloud_report_time=None,
        now_epoch=1_800_000_010,
        now_mono=110.0,
    )
    assert departing["mqtt_position"] is None
    assert departing["position_override"]["source"] == "trusted_previous"
    assert departing["position_override"]["value"] == {
        "x": 1.1,
        "y": 2.0,
        "heading": 4.7,
    }
    assert trust.position_trust_diagnostics(owner)["departure_waiting"] is True


def test_departure_releases_on_post_transition_vendor_pose() -> None:
    owner = Owner()

    accepted = trust.prepare_position_candidates(
        owner,
        snapshot(docked=True),
        mqtt_position={"x": 1.1, "y": 2.0, "heading": 4.7},
        cloud_position=None,
        mqtt_pose_time=1_700_000_000,
        cloud_report_time=None,
        now_epoch=1_800_000_000,
        now_mono=100.0,
    )
    trust.record_position_result(
        owner,
        snapshot(docked=True),
        {
            "navigation": {
                "position": {
                    "value": accepted["mqtt_position"],
                    "source": "official_mqtt",
                }
            }
        },
    )

    # First active snapshot starts the departure gate and holds the dock pose.
    first = trust.prepare_position_candidates(
        owner,
        snapshot(docked=False),
        mqtt_position={"x": 20.0, "y": 30.0, "heading": 1.0},
        cloud_position=None,
        mqtt_pose_time=1_700_000_000,
        cloud_report_time=None,
        now_epoch=1_800_000_010,
        now_mono=110.0,
    )
    trust.record_position_result(
        owner,
        snapshot(docked=False),
        {
            "navigation": {
                "position": first["position_override"],
            }
        },
    )

    fresh = trust.prepare_position_candidates(
        owner,
        snapshot(docked=False),
        mqtt_position={"x": 1.8, "y": 2.6, "heading": 4.5},
        cloud_position=None,
        mqtt_pose_time=1_800_000_012,
        cloud_report_time=None,
        now_epoch=1_800_000_013,
        now_mono=113.0,
    )
    assert fresh["mqtt_position"] == {"x": 1.8, "y": 2.6, "heading": 4.5}
    assert fresh["position_override"] is None
    diag = trust.position_trust_diagnostics(owner)
    assert diag["phase"] == "ready"
    assert diag["departure_waiting"] is False
    assert diag["last_reason"] == "post_departure_vendor_pose"
