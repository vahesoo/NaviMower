"""Regression contracts for Navimower 0.4.1-beta11."""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

import zstandard

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _probe():
    path = COMPONENT / "error_payload.py"
    spec = importlib.util.spec_from_file_location("navimower_beta11_error_payload", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_beta11_runtime_dependency_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta11"
    assert "zstandard==0.25.0" in manifest["requirements"]
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta11.md").read_text()
    assert "Zstandard" in notes
    assert "runtime" in notes
    assert "Problem" in notes
    assert "Error" in notes


def test_hint_error_probe_decodes_base64_zstd_json() -> None:
    probe = _probe()
    payload = {
        "errors": [
            {"code": 6113, "message": "Mower docking error"},
            {"code": 1105, "message": "Mowing motor stall protection"},
        ],
        "catalog": True,
    }
    compressed = zstandard.ZstdCompressor().compress(json.dumps(payload).encode())
    raw = base64.b64encode(compressed).decode()
    result = probe.inspect_hint_error_payload(raw)
    assert [layer["operation"] for layer in result["layers"]] == ["base64", "zstd"]
    assert result["decoded"]["kind"] == "json"
    assert result["decoded"]["decoded_data"] == payload
    assert "6113" in result["decoded"]["code_candidates"]
    assert "1105" in result["decoded"]["code_candidates"]


def test_runtime_probe_keeps_problem_entities_diagnostic_only() -> None:
    coordinator = (COMPONENT / "coordinator.py").read_text()
    assert "hint catalog is diagnostic only" in coordinator
    assert "authoritative problem signals are inline private errors, private 0302" in coordinator
