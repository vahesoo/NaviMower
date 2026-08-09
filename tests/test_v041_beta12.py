"""Regression contracts for Navimower 0.4.1-beta12."""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

from compression import zstd

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _probe():
    path = COMPONENT / "error_payload.py"
    spec = importlib.util.spec_from_file_location("navimower_beta12_error_payload", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_beta12_uses_python314_stdlib_zstd() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta12"
    assert all(not requirement.startswith("zstandard") for requirement in manifest["requirements"])

    payload = {"code": 6108, "message": "Mower got stuck"}
    raw = base64.b64encode(zstd.compress(json.dumps(payload).encode())).decode()
    result = _probe().inspect_hint_error_payload(raw)

    assert [layer["operation"] for layer in result["layers"]] == ["base64", "zstd"]
    assert all("decode_error" not in layer for layer in result["layers"])
    assert result["decoded"]["kind"] == "json"
    assert result["decoded"]["decoded_data"] == payload
    assert "6108" in result["decoded"]["code_candidates"]
