"""Regression contracts for Navimower 0.4.1-beta13."""
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
    spec = importlib.util.spec_from_file_location("navimower_beta13_error_payload", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_beta13_release_keeps_zstd_optional() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert all(not requirement.startswith("zstandard") for requirement in manifest["requirements"])
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta13.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta13")
    assert "libzstd" in notes


def test_beta13_normal_decoder_reports_backend() -> None:
    payload = {"code": 6113, "message": "Mower docking error"}
    raw = base64.b64encode(zstd.compress(json.dumps(payload).encode())).decode()
    result = _probe().inspect_hint_error_payload(raw)

    assert [layer["operation"] for layer in result["layers"]] == ["base64", "zstd"]
    assert result["layers"][1]["backend"] == "compression.zstd"
    assert result["decoded"]["kind"] == "json"
    assert result["decoded"]["decoded_data"] == payload
    assert "6113" in result["decoded"]["code_candidates"]


def test_beta13_native_backend_is_third_optional_fallback(monkeypatch) -> None:
    probe = _probe()
    expected = b'{"code":6108,"message":"Mower got stuck"}'

    def unavailable(_data: bytes) -> bytes:
        raise ImportError("not available in target runtime")

    monkeypatch.setattr(probe, "_zstd_stdlib", unavailable)
    monkeypatch.setattr(probe, "_zstd_third_party", unavailable)
    monkeypatch.setattr(probe, "_zstd_native", lambda _data: expected)

    decoded, backend = probe._zstd(b"zstd-frame-placeholder")
    assert decoded == expected
    assert backend == "libzstd"


def test_beta13_native_backend_uses_bounded_libzstd_api() -> None:
    source = (COMPONENT / "error_payload.py").read_text()
    assert 'ctypes.util.find_library("zstd")' in source
    assert "ZSTD_getFrameContentSize" in source
    assert "ZSTD_decompressBound" in source
    assert "ZSTD_decompress" in source
    assert "ZSTD_isError" in source
    assert "_MAX_DECODED_BYTES" in source
