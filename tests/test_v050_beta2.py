"""Release contract for canonical shadow hardening beta2."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta2_version_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta2"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta2.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "same vendor start",
        "canonical enrichment",
        "mqtt_pose_missing",
        "0.4.0-beta2",
        "shadow",
    ):
        assert marker in notes


def test_beta2_stays_non_authoritative() -> None:
    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    core = (COMPONENT / "canonical_state.py").read_text(encoding="utf-8")
    assert "field parity hardens" in runtime
    assert '"public_owner": "legacy_runtime"' in core
    assert 'CANONICAL_MODE = "shadow_beta2"' in core
