"""Synthetic H1 location regressions; no live account or mower is required."""
from __future__ import annotations

import ast
import importlib.util
import importlib
import io
import json
from pathlib import Path
import sys
import types
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/navimower"


def load(name):
    spec = importlib.util.spec_from_file_location(f"h1_{name}", COMPONENT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


location = load("location")
fallback = load("position_fallback")
presence = load("app_presence")
canonical = load("canonical_state")


def packet(stamp=1000, **fields):
    return {"type": 1, "postureX": 1, "postureY": 2, "postureTheta": 0.5, "time": stamp, **fields}


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), -99999, True, "bad"])
def test_invalid_pose_cannot_partially_overwrite_or_poison_ordering(bad):
    cache = {}
    location.parse_location_payload(cache, "test", [packet()])
    assert location.parse_location_payload(cache, "test", [packet(2000, postureY=bad)]) is None
    assert (cache["test"]["x"], cache["test"]["y"], cache["test"]["pose_time"]) == (1, 2, 1000)
    assert cache["test"]["_pose_updated"] is False
    assert location.parse_location_payload(cache, "test", [packet(1500, postureX=3)])["x"] == 3


def test_partial_pose_and_progress_do_not_refresh_cached_pose():
    cache = {}
    location.parse_location_payload(cache, "test", [packet()])
    result = location.parse_location_payload(cache, "test", [
        {"type": 1, "postureX": 9, "time": 2000},
        {"type": 2, "currentMowProgress": 45},
    ])
    assert (result["x"], result["y"], result["theta"], result["pose_time"]) == (1, 2, 0.5, 1000)
    assert result["_pose_updated"] is False
    assert result["_progress_updated"] is True


def test_missing_time_does_not_inherit_previous_measurement_time():
    cache = {}
    location.parse_location_payload(cache, "test", [packet()])
    row = packet(postureX=0)
    del row["time"]
    result = location.parse_location_payload(cache, "test", [row])
    assert result["x"] == 0
    assert result["pose_time"] is None


def test_future_timestamp_does_not_poison_next_valid_pose(monkeypatch):
    monkeypatch.setattr(location.time, "time", lambda: 1000)
    cache = {}
    assert location.parse_location_payload(cache, "test", [packet(2000)]) is None
    result = location.parse_location_payload(cache, "test", [packet(999)])
    assert result["pose_time"] == 999


@pytest.mark.parametrize("reverse", [False, True])
def test_current_cloud_pose_wins_independently_of_key_order(reverse):
    row = {"last_posture_x": 9, "last_posture_y": 8, "last_posture_theta": 7,
           "posture_x": 0, "posture_y": 2, "posture_theta": 1, "report_time": 1000}
    if reverse:
        row = dict(reversed(list(row.items())))
    pose = location.parse_cloud_pose(row)
    assert pose == {"position": {"x": 0, "y": 2, "heading": 1}, "time": 1000, "kind": "current"}


def test_cloud_never_mixes_records_or_current_and_last_axes():
    assert location.parse_cloud_pose({"a": {"posture_x": 1}, "b": {"posture_y": 2}})["position"] is None
    result = location.parse_cloud_pose({"posture_x": 1, "last_posture_y": 2})
    assert result["position"] is None
    result = location.parse_cloud_pose({"posture_x": -99999, "posture_y": -99999,
                                       "last_posture_x": 9, "last_posture_y": 8, "report_time": 1000})
    assert result["kind"] == "last_known"
    assert result["time"] is None
    choice = fallback.choose_position(mqtt_position=None, mqtt_age=None,
                                      cloud_position=result["position"], cloud_report_time=result["time"], now_epoch=1001)
    assert choice["stale"] is True
    assert choice["gate_usable"] is False


def test_cloud_route_vertices_are_never_a_live_position():
    result = location.parse_cloud_pose({"path": [{"x": 9, "y": 8}], "report_time": 1000})
    assert result["position"] is None
    result = location.parse_cloud_pose({"data": {"postureX": 1, "postureY": 2, "report_time": 1000}})
    assert result["position"]["x"] == 1
    assert result["time"] == 1000


def coordinator_methods(*names, clock):
    """Execute the actual coordinator freshness methods without importing HA."""
    tree = ast.parse((COMPONENT / "coordinator.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "NavimowCoordinator")
    methods = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {"time": clock, "MQTT_POSE_STALE_SECONDS": 20,
                 "valid_coordinate": location.valid_coordinate, "_as_float": location.valid_coordinate}
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(COMPONENT / "coordinator.py"), "exec"), namespace)
    return type("CoordinatorFreshness", (), {name: namespace[name] for name in names})()


def test_recent_receipt_is_not_a_recent_measurement():
    clock = SimpleNamespace(monotonic=lambda: 100)
    owner = coordinator_methods("pose_age", "_fresh_mqtt_position", "mqtt_message_age", clock=clock)
    owner._mqtt_location = {"x": 1, "y": 2, "theta": 0}
    owner._mqtt_last_update = 100
    owner._mqtt_last_message_update = 100
    owner._mqtt_pose_age_at_receipt = fallback.cloud_report_age(700, now_epoch=1000)
    assert owner.mqtt_message_age() == 0
    assert owner.pose_age() == 300
    assert owner._fresh_mqtt_position() is None
    owner._mqtt_pose_age_at_receipt = 2
    assert owner._fresh_mqtt_position()["x"] == 1
    clock.monotonic = lambda: 121
    assert owner.pose_age() == 23
    assert owner._fresh_mqtt_position() is None
    owner._mqtt_pose_age_at_receipt = None
    assert owner.pose_age() is None
    assert owner._fresh_mqtt_position() is None


def test_unknown_measurement_age_is_never_published_as_live():
    result = canonical.resolve_position(mqtt_position={"x": 1, "y": 2}, mqtt_pose_age_s=None,
                                         cloud_position={"x": 3, "y": 4}, cloud_position_age_s=None)
    assert result["source"] == "private_cloud"
    assert result["stale"] is True


def test_presence_is_bounded_failure_isolated_and_stops_on_unload():
    calls = []
    def send(sn):
        calls.append(sn)
        if len(calls) == 1:
            raise RuntimeError("synthetic failure")
    owner = SimpleNamespace(client=SimpleNamespace(app_active=send), sn="synthetic")
    presence.report_if_due(owner, 0)
    presence.report_if_due(owner, 299)
    assert calls == ["synthetic"]
    assert presence.diagnostics(owner, 299)["last_result"] == "failed"
    presence.report_if_due(owner, 300)
    assert len(calls) == 2
    assert presence.diagnostics(owner, 301)["last_success_age_s"] == 1
    owner._shutdown_complete = True
    presence.report_if_due(owner, 600)
    assert len(calls) == 2


def test_client_requests_only_app_alive_and_precise_location():
    tree = ast.parse((COMPONENT / "api/client.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "NavimowCloudClient")
    methods = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in {"app_active", "location"}]
    namespace = {"Any": object}
    exec(compile(ast.Module(body=methods, type_ignores=[]), "client.py", "exec"), namespace)
    calls = []
    owner = SimpleNamespace(call=lambda *args, **kwargs: calls.append((args, kwargs)))
    namespace["app_active"](owner, "synthetic")
    namespace["location"](owner, "synthetic", 1)
    assert calls[0] == (("/mowerbot/vehicle/vehicle/state", {"vehicle_sn": "synthetic", "type": 1, "stateList": [{"state": 1}]}), {"retry_auth": False})
    assert calls[1][0][1]["need_precise_location"] == 2


def test_presence_uses_authenticated_envelope_and_regional_json_transport(monkeypatch):
    package = types.ModuleType("_h1_transport_test")
    package.__path__ = [str(COMPONENT)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    api = importlib.import_module(f"{package.__name__}.api")
    crypto = importlib.import_module(f"{package.__name__}.api.crypto")
    requests, bodies = [], []

    def pack(body):
        bodies.append(body)
        return {"p": "101", "d": "synthetic-encrypted-payload"}

    def urlopen(request, **kwargs):
        requests.append(request)
        return io.BytesIO(b'{"code":1,"data":{}}')

    monkeypatch.setattr(crypto, "pack", pack)
    monkeypatch.setattr(api.urllib.request, "urlopen", urlopen)
    client = api.NavimowCloudClient("synthetic-device", tokens=api.Tokens("synthetic-token", "synthetic-refresh"),
                                    uid="synthetic-user", region="sg")
    assert client.app_active("synthetic-mower") == {}
    assert requests[0].full_url == f"https://{client.host}/mowerbot/vehicle/vehicle/state"
    assert requests[0].get_header("Content-type") == "application/json"
    assert json.loads(requests[0].data) == {"p": "101", "d": "synthetic-encrypted-payload"}
    assert bodies[0]["uid"] == "synthetic-user"
    assert bodies[0]["access_token"] == "synthetic-token"
    assert bodies[0]["stateList"] == [{"state": 1}]
