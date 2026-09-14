from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "navimower"
    / "private_api_probe.py"
)
SPEC = importlib.util.spec_from_file_location("navimower_private_api_probe_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
probe_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe_module)

PROBE_CHOICES = probe_module.PROBE_CHOICES
_build_probe_document = probe_module._build_probe_document
_probe_trail = probe_module._probe_trail
_sanitize_probe_value = probe_module._sanitize_probe_value


class DummyClient:
    _language = "en"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, path: str, request: dict):
        self.calls.append((path, request))
        if path == "/vehicle/trail/get-path-info-time":
            return [
                {"partitionId": 41, "partitionPercentage": 100},
                {"partitionId": "42", "partitionPercentage": 30},
                {"partitionId": 41, "partitionPercentage": 100},
            ]
        if path == "/vehicle/trail/get-path-info-data-compress":
            return "base64-zstd-placeholder"
        if path == "/vehicle/report/vehicle-main-report":
            return {"totalMowingArea": 123}
        if path == "/vehicle/report/get-day-week-month-data":
            return {"queryType": request["query_type"]}
        if path == "/mowerbot/map/queryDynamicsMap":
            if "vehicle_sn" in request:
                raise RuntimeError("vehicle_sn variant rejected")
            return {"mapDetail": "opaque", "mapVersion": 9}
        if path == "/mowerbot/vehicle/common/get-iot-file":
            return {"url": "https://example.invalid/terrain", "version": 7}
        if path == "/vehicle/vehicle/get-vehicle-weather":
            return {"rainState": 0}
        if path == "/mowerbot/vehicle/rtk/queryRtkService":
            return {
                "rtkServiceStatus": 1,
                "rtkAccount": "secret-account",
                "rtkPassword": "secret-password",
            }
        raise AssertionError(path)


def test_probe_choices_are_bounded_read_only_families() -> None:
    assert PROBE_CHOICES == ("all", "trail", "reports", "terrain", "weather", "rtk")


def test_trail_probe_uses_partition_list_array() -> None:
    client = DummyClient()
    result = _probe_trail(client, "SN123")

    assert result["partition_ids"] == [41, 42]
    assert result["path_info_data_compress"]["ok"] is True
    assert client.calls[-1] == (
        "/vehicle/trail/get-path-info-data-compress",
        {"vehicle_sn": "SN123", "partitionList": [41, 42]},
    )


def test_rtk_credentials_are_redacted_recursively() -> None:
    value = {
        "rtkAccount": "account",
        "nested": {"rtk_password": "password", "status": 1},
    }
    assert _sanitize_probe_value(value) == {
        "rtkAccount": "**REDACTED**",
        "nested": {"rtk_password": "**REDACTED**", "status": 1},
    }


def test_all_probe_keeps_partial_variant_failures_and_sanitizes_rtk() -> None:
    client = DummyClient()
    coordinator = SimpleNamespace(
        client=client,
        sn="SN123",
        vehicle_type=9,
        entry=SimpleNamespace(entry_id="entry-1"),
    )

    document = _build_probe_document(coordinator, "all")

    assert set(document["probes"]) == {"trail", "reports", "terrain", "weather", "rtk"}
    terrain = document["probes"]["terrain"]["dynamic_map"]
    assert terrain["ok"] is True
    assert terrain["selected_attempt"] == 1
    assert terrain["attempts"][0]["ok"] is False
    assert terrain["attempts"][1]["request"] == {"sn": "SN123"}

    rtk_attempt = document["probes"]["rtk"]["attempts"][0]["data"]
    assert rtk_attempt["rtkAccount"] == "**REDACTED**"
    assert rtk_attempt["rtkPassword"] == "**REDACTED**"
