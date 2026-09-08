"""Behavioral diagnostics privacy regressions using synthetic data only.

No actual account, mower identifier, garden geometry or uploaded diagnostics is
committed. These tests execute the sanitizer and the loaded/unloaded Download
handler rather than relying on inert research-code markers.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "navimower"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sanitizer = _load("_privacy_sanitizer", COMPONENT / "diagnostics_sanitize.py")


@pytest.mark.parametrize("key", [
    "last_latitude", "lastLongitude", "GPSLatitude", "gpsLAT", "gpsCoords",
    "nextGpsPosition", "pin_code", "pinCode", "newPINCode", "iccid", "simICCID",
    "imei", "imsi", "msisdn", "antiTheftPoint", "antiTheftRadius", "editMapUid",
    "editMapUID", "vehicleSn", "vehicleSN", "serialNumber", "deviceId",
    "oauthDeviceID", "userID", "accountId", "mowerId", "vehicleId", "clientId",
    "apiKey", "api_key", "sessionKey", "session_key", "privateKey", "signingKey",
    "clientKey", "encryptionKey", "futureApiKey", "newAccessToken", "accesstoken",
    "session_secret", "SESSION_SECRET", "authORIZATION", "cookie", "setCookie",
    "email", "userName", "wifiSSID", "wifiBSSID", "ipAddress", "macAddress",
    "nested.user_id", "phoneNumber", "pwdInfo", "password", "last_longitude",
])
def test_sensitive_names_are_redacted_at_every_nesting(key):
    source = {"outer": [{key: "synthetic-sensitive-value"}]}
    original = deepcopy(source)
    assert sanitizer.sanitize(source) == {"outer": [{key: sanitizer.REDACTED}]}
    assert source == original


@pytest.mark.parametrize("key", [
    "mapping", "spinning", "map_id", "mapId", "mapVersion", "partitionIds",
    "zone_id", "task_id", "countryCode", "isCutterHeight", "mowingHeightList",
    "map_area_limit", "height", "mowingPathWidth", "chargingLimit", "startPlan",
    "active_zone_id", "last_completed_at", "mqtt_pose_age", "work_target_zone",
    "mqtt_connected", "source", "error_code", "code", "type", "headlight",
])
def test_support_fields_are_not_redacted_by_short_substrings(key):
    assert sanitizer.sanitize({key: [30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80]}) == {
        key: [30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80]
    }


def test_nested_json_and_case_variants():
    source = {"payload": json.dumps({"nested": [{"editMapUID": "private-map-editor", "pinCode": 4321}], "mapId": 17})}
    clean = sanitizer.sanitize(source)
    assert clean["payload"]["nested"][0] == {"editMapUID": sanitizer.REDACTED, "pinCode": sanitizer.REDACTED}
    assert clean["payload"]["mapId"] == 17


def test_free_text_credentials_and_network_values():
    source = {"error": "denied Bearer synthetic.jwt.value email demo@example.invalid apiKey=abc123 pin_code=4321 from 192.0.2.4 12:34:56:78:90:ab"}
    text = sanitizer.sanitize(source)["error"]
    for private in ("synthetic.jwt.value", "demo@example.invalid", "abc123", "4321", "192.0.2.4", "12:34:56:78:90:ab"):
        assert private not in text
    assert "denied" in text


def test_url_origin_only_without_losing_surrounding_message():
    source = {"error": "request https://alice:password@api.example.invalid/devices/private-id?token=private-token#private failed"}
    assert sanitizer.sanitize(source)["error"] == "request https://api.example.invalid failed"
    assert sanitizer.sanitize({"url": "https://[2001:db8::1]:443/private"})["url"] == "https://redacted-host:443"
    assert sanitizer.sanitize({"url": "https://api.example.invalid:bad/private"})["url"] == "<redacted-url>"


def test_known_identifiers_echoed_in_values_and_dictionary_keys():
    source = {
        "vehicleSn": "DEMO-MOWER-ONLY",
        "error": "problem on DEMO-MOWER-ONLY",
        "topics": {
            "/downlink/vehicle/DEMO-MOWER-ONLY/state": {"code": 6108},
        },
    }
    clean = sanitizer.sanitize(source)
    assert "DEMO-MOWER-ONLY" not in json.dumps(clean)
    assert clean["topics"]["/downlink/vehicle/<redacted>/state"]["code"] == 6108
    context_clean = sanitizer.sanitize({"error": "token echo DEMO-TOKEN-ONLY"}, sensitive_values={"accessToken": "DEMO-TOKEN-ONLY"})
    assert "DEMO-TOKEN-ONLY" not in context_clean["error"]


def test_dictionary_redaction_collisions_preserve_both_records():
    source = {"users": {"one@example.invalid": {"state": 1}, "two@example.invalid": {"state": 2}}}
    values = list(sanitizer.sanitize(source)["users"].values())
    assert values == [{"state": 1}, {"state": 2}]


def test_binary_long_opaque_and_unknown_values_do_not_echo_contents():
    class PrivateObject:
        def __repr__(self):
            raise AssertionError("Object repr must never be used in support diagnostics")
    clean = sanitizer.sanitize({"bytes": b"private binary", "long": "a" * 20000, "opaque": "Q" * 512, "unknown": PrivateObject(), "bad_float": float("nan")})
    assert clean["bytes"]["_omitted"] == "bytes"
    assert clean["long"]["_omitted"] == "large_string"
    assert clean["opaque"]["_omitted"] == "opaque_string"
    assert clean["unknown"] == {"_omitted": "unsupported_type", "type": "PrivateObject"}
    assert clean["bad_float"] is None
    json.dumps(clean, allow_nan=False)


def test_recursion_is_bounded_and_sanitizing_is_idempotent():
    cyclic = {}; cyclic["self"] = cyclic
    json.dumps(sanitizer.sanitize(cyclic))
    source = {"email": "demo@example.invalid", "entry": {"pin_code": "4321"}, "map_id": "demo-map", "error": "Bearer abc.def.ghi", "url": "https://example.invalid/private"}
    clean = sanitizer.sanitize(source)
    assert sanitizer.sanitize(clean) == clean


def test_exclusions_apply_only_when_requested_and_do_not_touch_source():
    source = {"raw": {"error_h5_discovery": {"snippet": "PRIVATE_RESEARCH"}, "command_discovery": {"snippet": "PRIVATE_COMMAND"}, "mapId": 12}}
    original = deepcopy(source)
    clean = sanitizer.sanitize(source, exclude_keys=frozenset({"error_h5_discovery", "command_discovery"}))
    assert clean == {"raw": {"mapId": 12}}
    assert "error_h5_discovery" in sanitizer.sanitize(source)["raw"]
    assert source == original


@pytest.fixture
def diagnostics(monkeypatch):
    """Import the real handler without booting Home Assistant or its runtime."""
    package = "_navimower_privacy_test"
    root = ModuleType(package); root.__path__ = [str(COMPONENT)]
    monkeypatch.setitem(sys.modules, package, root)
    monkeypatch.setitem(sys.modules, package + ".diagnostics_sanitize", sanitizer)
    for name, attrs in {
        "homeassistant": {},
        "homeassistant.config_entries": {"ConfigEntry": object},
        "homeassistant.core": {"HomeAssistant": object},
        package + ".const": {"DOMAIN": "navimower", "OPT_GOOGLE_MAPS_API_KEY": "google_maps_api_key"},
        package + ".capability_profile": {"build_capability_profile": lambda data: {}},
        package + ".map_underlay": {"map_underlay_diagnostics": lambda coordinator: {"configured": False}},
        package + ".private_cloud_region": {"private_cloud_region_diagnostics": lambda coordinator: {"region": "fra"}},
        package + ".state_semantics": {"error_transition_diagnostics": lambda coordinator: {"code": 6108}},
    }.items():
        stub = ModuleType(name)
        for key, value in attrs.items(): setattr(stub, key, value)
        monkeypatch.setitem(sys.modules, name, stub)
    return _load(package + ".diagnostics", COMPONENT / "diagnostics.py")


def _entry():
    return SimpleNamespace(
        entry_id="synthetic-entry",
        data={"access_token": "DEMO-TOKEN-ONLY", "vehicle_sn": "DEMO-MOWER-ONLY", "model": "H-test"},
        options={"google_maps_api_key": "DEMO-GOOGLE-KEY", "chargingLimit": 100},
    )


def test_unloaded_download_cleans_entry_and_options(diagnostics):
    entry = _entry()
    entry.data["error"] = "echo DEMO-GOOGLE-KEY"
    hass = SimpleNamespace(data={})
    report = asyncio.run(diagnostics.async_get_config_entry_diagnostics(hass, entry))
    assert report["cached_only"] is True
    assert report["redaction_version"] == sanitizer.REDACTION_VERSION
    assert report["entry"]["data"]["access_token"] == sanitizer.REDACTED
    assert "google_maps_api_key" not in report["entry"]["options"]
    assert "DEMO-GOOGLE-KEY" not in json.dumps(report)
    assert entry.data["access_token"] == "DEMO-TOKEN-ONLY"


def test_loaded_download_is_cached_only_and_omits_retired_research(diagnostics):
    entry = _entry()
    class NoIO:
        def __getattr__(self, name):
            raise AssertionError(f"Download must not call a vendor, raw export or executor: {name}")
    data = {
        "model": "H-test", "battery": 84, "state_code": "0104", "x": 2.0, "y": 3.0,
        "capabilities": {"cutting_height": {"supported": True}},
        "map": {"map_id": "test-map", "zones": [{"id": 36}], "off_limit_areas": [[[0, 0], [1, 0], [0, 1]]]},
        "zone_states": [{"id": 36, "last_completed_at": "2026-09-01T10:00:00+00:00"}],
        "settings": {"mowingHeightList": list(range(30, 85, 5)), "pinCode": 4321},
        "private_cloud_error": "echo DEMO-MOWER-ONLY and DEMO-TOKEN-ONLY",
        "raw": {
            "index2": {"mapVersion": 11, "editMapInfo": {"editMapUid": "DEMO-EDITOR-ONLY", "editMapChannel": 2}},
            "location": {"lastLatitude": 12.345, "lastLongitude": 67.891},
            "device_info": {"simICCID": "DEMO-SIM-ONLY", "map_area_limit": 500},
            "maintenance_h5_discovery": {"snippet": "PRIVATE_RESEARCH"},
            "nested": {"command_discovery": {"snippet": "PRIVATE_COMMAND"}},
        },
    }
    controller = SimpleNamespace(diagnostics=lambda: {"active_zone_id": 36, "resume_pending": True, "charging_limit_reached_at": "2026-09-01T10:00:00+00:00"})
    bridge = SimpleNamespace(
        diagnostic_health=lambda: {"connected": True},
        diagnostic_inventory=lambda: {"/downlink/vehicle/DEMO-MOWER-ONLY/state": {"count": 5}},
        diagnostic_discovery=lambda: {"samples": [{"newApiKey": "DEMO-SAMPLE-KEY"}]},
    )
    coordinator = SimpleNamespace(data=data, sn="DEMO-MOWER-ONLY", client=NoIO(), mqtt_bridge=bridge,
        navimower_schedule=controller, _mqtt_location={"work_target_zone": 36},
        _notification_raw_cache={"userName": "DEMO-USER-ONLY"})
    hass = SimpleNamespace(data={"navimower": {entry.entry_id: coordinator}}, async_add_executor_job=NoIO())
    original = deepcopy(data)
    report = asyncio.run(diagnostics.async_get_config_entry_diagnostics(hass, entry))
    encoded = json.dumps(report)
    for private in ("DEMO-MOWER-ONLY", "DEMO-TOKEN-ONLY", "DEMO-EDITOR-ONLY", "DEMO-SIM-ONLY", "DEMO-SAMPLE-KEY", "DEMO-USER-ONLY", "PRIVATE_RESEARCH", "PRIVATE_COMMAND"):
        assert private not in encoded
    for retired in ("maintenance_h5_discovery", "error_h5_discovery", "command_discovery"):
        assert retired not in encoded
    assert report["map_edit"]["edit_session_active"] is True
    assert report["map_edit"]["edit_map_info"]["editMapUid"] == sanitizer.REDACTED
    assert report["map"]["map_id"] == "test-map"
    assert report["positioning"]["x"] == 2.0
    assert report["map"]["off_limit_areas"][0]["point_count"] == 3
    assert report["telemetry"]["battery"] == 84
    assert report["navimower_schedule"]["active_zone_id"] == 36
    assert report["telemetry"]["zone_states"][0]["last_completed_at"] == "2026-09-01T10:00:00+00:00"
    assert report["settings"]["mowingHeightList"] == list(range(30, 85, 5))
    assert data == original
    assert entry.options["google_maps_api_key"] == "DEMO-GOOGLE-KEY"
