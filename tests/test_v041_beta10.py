"""Regression contracts for Navimower 0.4.1-beta10."""
from __future__ import annotations

import base64
import gzip
import importlib.util
import json
from pathlib import Path
import zlib

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _probe():
    path = COMPONENT / "error_payload.py"
    spec = importlib.util.spec_from_file_location("navimower_beta10_error_payload", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_beta10_notes_remain_available() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta10.md").read_text()
    assert "get-hint-error-compress" in notes
    assert "Base64" in notes
    assert "Zstandard" in notes
    assert "Problem" in notes
    assert "Error" in notes


def test_hint_error_probe_decodes_base64_gzip_json() -> None:
    probe = _probe()
    payload = {
        "errors": [{"code": 6113, "message": "Failed to dock"}],
        "vehicle_sn": "SECRET-MOWER",
    }
    raw = base64.b64encode(gzip.compress(json.dumps(payload).encode())).decode()
    result = probe.inspect_hint_error_payload(raw, redactions=("SECRET-MOWER",))
    assert [layer["operation"] for layer in result["layers"]] == ["base64", "gzip"]
    assert result["decoded"]["decoded_data"] == payload
    assert "6113" in result["decoded"]["code_candidates"]


def test_hint_error_probe_decodes_base64_zlib_json() -> None:
    probe = _probe()
    payload = {"code": 1105, "title": "Mowing motor stall protection"}
    raw = base64.b64encode(zlib.compress(json.dumps(payload).encode())).decode()
    result = probe.inspect_hint_error_payload(raw)
    assert [layer["operation"] for layer in result["layers"]] == ["base64", "zlib"]
    assert result["decoded"]["decoded_data"] == payload
    assert "1105" in result["decoded"]["code_candidates"]


def test_hint_error_probe_keeps_unknown_binary_bounded() -> None:
    probe = _probe()
    raw = base64.b64encode(b"\x00\x01\x02fault=6108 mower stuck\x00\xff").decode()
    result = probe.inspect_hint_error_payload(raw)
    assert result["decoded"]["kind"] == "binary"
    assert "6108" in result["decoded"]["code_candidates"]


def test_api_wrapper_keeps_problem_logic_diagnostic_only() -> None:
    api = (COMPONENT / "api" / "__init__.py").read_text()
    coordinator = (COMPONENT / "coordinator.py").read_text()
    assert "inspect_hint_error_payload" in api
    assert '"endpoint": "/vehicle/vehicle/get-hint-error-compress"' in api
    assert "authoritative problem signals are inline private errors, private 0302" in coordinator
    assert "hint catalog is diagnostic only" in coordinator
