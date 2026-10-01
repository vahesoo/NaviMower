"""Release contract for the paired canonical-architecture beta1."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def test_beta1_version_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0-beta1"
    notes = (ROOT / ".github" / "release-notes" / "0.5.0-beta1.md").read_text(encoding="utf-8")
    assert notes.startswith("title: Navimower 0.5.0-beta1\n")
    for marker in ("Canonical Mower State", "shadow", "sparse-MQTT H1", "0.4.0-beta1", "No public entity"):
        assert marker in notes


def test_beta1_canonical_shadow_is_non_authoritative() -> None:
    runtime = (COMPONENT / "runtime.py").read_text(encoding="utf-8")
    shadow = (COMPONENT / "canonical_shadow_semantics.py").read_text(encoding="utf-8")
    core = (COMPONENT / "canonical_state.py").read_text(encoding="utf-8")
    diagnostics = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert "install_canonical_shadow_semantics" in runtime
    assert '"public_owner": "legacy_runtime"' in core
    assert "_canonical_shadow_diagnostics" in shadow
    assert '"canonical_v2": canonical_v2' in diagnostics
    assert "coordinates_included" in core
