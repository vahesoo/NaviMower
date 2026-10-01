"""Release contract for Navimower 0.4.7-beta4."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta4_version_and_release_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.7-beta4"
    notes = (ROOT / ".github" / "release-notes" / "0.4.7-beta4.md").read_text(
        encoding="utf-8"
    )
    assert notes.startswith("title: Navimower 0.4.7-beta4\n")
    for marker in (
        "navimower.probe_iot_file",
        "/mowerbot/vehicle/common/get-iot-file",
        "0, 1, 2, 3, 4, 5",
        "16 MiB",
        "never persisted",
        "removed again before stable promotion",
    ):
        assert marker in notes


def test_beta4_probe_surface_is_fixed_and_bounded() -> None:
    probe = (COMPONENT / "iot_file_probe.py").read_text(encoding="utf-8")
    services = (COMPONENT / "services.py").read_text(encoding="utf-8")
    yaml = (COMPONENT / "services.yaml").read_text(encoding="utf-8")

    assert 'IOT_FILE_ENDPOINT = "/mowerbot/vehicle/common/get-iot-file"' in probe
    assert "DEFAULT_IOT_FILE_TYPES = (0, 1, 2, 3, 4, 5)" in probe
    assert "IOT_FILE_TYPE_MIN = 0" in probe
    assert "IOT_FILE_TYPE_MAX = 15" in probe
    assert "IOT_FILE_MAX_TYPES_PER_RUN = 8" in probe
    assert "IOT_FILE_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024" in probe
    assert "arbitrary path or payload" in probe

    assert 'SERVICE_PROBE_IOT_FILE = "probe_iot_file"' in services
    assert "PROBE_IOT_FILE_SCHEMA" in services
    assert "async_probe_iot_file" in services
    assert "probe_iot_file:" in yaml
    assert "download_artifacts" in yaml


def test_beta4_probe_never_persists_signed_vendor_url() -> None:
    probe = (COMPONENT / "iot_file_probe.py").read_text(encoding="utf-8")
    assert '_REDACTED_SIGNED_URL = "**REDACTED_SIGNED_URL**"' in probe
    assert "row.pop(\"_signed_url_runtime\", None)" in probe
    assert "Signed download URLs are never persisted" in probe
    assert 're.sub(r"https?://\\\\S+", "<redacted-url>", str(err))' in probe


def test_beta4_keeps_arbitrary_probe_retired() -> None:
    services = (COMPONENT / "services.py").read_text(encoding="utf-8")
    assert not (COMPONENT / "private_api_probe.py").exists()
    assert "SERVICE_PROBE_PRIVATE_API" not in services
    assert "probe_private_api" not in services
