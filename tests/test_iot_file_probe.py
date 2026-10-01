"""Dependency-free tests for the bounded get-iot-file prerelease probe."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import sys
import types
import zipfile

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _load_probe():
    package_name = "beta4_iot_probe"
    package = types.ModuleType(package_name)
    package.__path__ = [str(COMPONENT)]
    sys.modules[package_name] = package

    target = COMPONENT / "iot_file_probe.py"
    spec = importlib.util.spec_from_file_location(
        f"{package_name}.iot_file_probe",
        target,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


def test_default_types_are_bounded_and_cover_initial_unknown_range() -> None:
    assert probe.DEFAULT_IOT_FILE_TYPES == (0, 1, 2, 3, 4, 5)
    assert probe._normalize_types(None) == [0, 1, 2, 3, 4, 5]
    assert probe._normalize_types([1, 3, 4, 3]) == [1, 3, 4]

    try:
        probe._normalize_types([0, 1, 2, 3, 4, 5, 6, 7, 8])
    except ValueError as err:
        assert "At most 8" in str(err)
    else:
        raise AssertionError("unbounded type list was accepted")

    try:
        probe._normalize_types([16])
    except ValueError as err:
        assert "0..15" in str(err)
    else:
        raise AssertionError("out-of-range type was accepted")


def test_probe_is_fixed_to_get_iot_file_and_redacts_signed_url() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls = []

        def call(self, path, request):
            self.calls.append((path, dict(request)))
            assert path == probe.IOT_FILE_ENDPOINT
            if request.get("type") == 3:
                return {
                    "version": "v3",
                    "sn": "TEST-SN",
                    "url": "https://example.invalid/signed/path?token=secret",
                    "kind": "mystery",
                }
            raise RuntimeError("unsupported")

    client = Client()
    row = probe._call_type(client, "TEST-SN", 3)
    assert row["ok"] is True
    assert row["version"] == "v3"
    assert row["signed_url_present"] is True
    assert row["signed_url_host"] == "example.invalid"
    assert row["response"]["url"] == probe._REDACTED_SIGNED_URL
    assert row["_signed_url_runtime"].startswith("https://example.invalid/")
    assert client.calls[0] == (
        probe.IOT_FILE_ENDPOINT,
        {"vehicle_sn": "TEST-SN", "type": 3},
    )


def test_probe_tries_sn_fallback_only_after_vehicle_sn_failure() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls = []

        def call(self, path, request):
            self.calls.append(dict(request))
            if "vehicle_sn" in request:
                raise RuntimeError("wrong key")
            return {"version": 7, "url": "https://example.invalid/file"}

    client = Client()
    row = probe._call_type(client, "ABC", 4)
    assert row["ok"] is True
    assert row["selected_attempt"] == 1
    assert client.calls == [
        {"vehicle_sn": "ABC", "type": 4},
        {"sn": "ABC", "type": 4},
    ]


def test_zip_artifact_inspection_keeps_names_and_hashes() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mystery/meta.json", '{"hello": "world"}')
        archive.writestr("mystery/data.bin", b"abc")

    result = probe._inspect_artifact(buffer.getvalue(), "application/zip")
    assert result["kind"] == "zip"
    assert result["zip_valid"] is True
    assert result["entry_count"] == 2
    names = {row["name"] for row in result["entries"]}
    assert names == {"mystery/meta.json", "mystery/data.bin"}
    assert len(result["sha256"]) == 64


def test_run_document_never_persists_runtime_signed_url(tmp_path) -> None:
    class Entry:
        entry_id = "entry"

    class Client:
        @staticmethod
        def call(path, request):
            assert path == probe.IOT_FILE_ENDPOINT
            return {
                "version": f"v{request['type']}",
                "url": f"https://example.invalid/type{request['type']}?token=secret",
                "sn": "PRIVATE-SN",
            }

    class Coordinator:
        entry = Entry()
        client = Client()
        sn = "PRIVATE-SN"
        vehicle_type = 123

    document, artifacts = probe._run_probe_blocking(
        Coordinator(),
        [1, 2, 3, 4],
        False,
        tmp_path,
        "stamp",
    )
    assert artifacts == []
    assert document["requested_file_types"] == [1, 2, 3, 4]
    assert [row["file_type"] for row in document["results"]] == [1, 2, 3, 4]
    rendered = str(document)
    assert "token=secret" not in rendered
    assert probe._REDACTED_SIGNED_URL in rendered
